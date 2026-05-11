"""Authenticated login with automatic strategy fallback and config-driven behavior."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


def _get_session_age_hours(session_file: Path) -> float:
    modified_at = datetime.fromtimestamp(session_file.stat().st_mtime, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - modified_at).total_seconds() / 3600


def _strip_spa_suffix(base_url: str) -> str:
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/spa"):
        base_path = base_path[:-4]
    return parsed._replace(path=base_path, params="", query="", fragment="").geturl().rstrip("/")


def _extract_cookie_domain(base_url: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.hostname:
        raise ValueError(f"Unable to extract domain from base_url: {base_url}")
    return parsed.hostname


def _session_file(config: dict[str, Any]) -> Path:
    return Path(config["session"]["state_file"])


def _debug_screenshot_path(config: dict[str, Any]) -> Path:
    return Path(config["output"]["debug_screenshot"])


def _must_not_contain(config: dict[str, Any]) -> str:
    return str(config["auth"]["form"]["success_check"]["must_not_contain"])


def _is_login_success_url(current_url: str, forbidden_fragment: str) -> bool:
    return forbidden_fragment.lower() not in current_url.lower()


def _log_input_elements(logger: logging.Logger, inputs: list[dict[str, str | None]]) -> None:
    for input_data in inputs:
        logger.debug(
            "Input found: id=%s name=%s type=%s placeholder=%s",
            input_data["id"],
            input_data["name"],
            input_data["type"],
            input_data["placeholder"],
        )


async def get_or_create_session(config: dict, credentials: dict) -> str:
    """Return a reusable authenticated storage state file path."""
    logger = logging.getLogger("authenticated_crawler")
    session_file = _session_file(config)
    max_age_hours = config["session"]["max_age_hours"]

    

    logger.info("🔐 Creating new session using auto-fallback chain...")

    strategies = (
        ("REST API", _login_rest),
        ("Playwright form", _login_playwright_form),
        ("Browser Use agent", _login_browser_use_agent),
    )

    for strategy_name, strategy_func in strategies:
        session_path = await strategy_func(config, credentials, logger)
        if session_path is not None:
            logger.info("✅ Login successful via %s", strategy_name)
            return session_path

        logger.warning("Login strategy failed: %s", strategy_name)

    raise Exception("All login strategies failed")


async def _login_rest(
    config: dict[str, Any],
    credentials: dict[str, str],
    logger: logging.Logger,
) -> str | None:
    session_file = _session_file(config)
    base_url = config["site"]["base_url"]
    auth_config = config["auth"]
    rest_endpoint = str(auth_config["rest_endpoint"])
    rest_auth_method = str(auth_config["rest_auth_method"]).lower()
    timeout_seconds = auth_config["form"]["wait_before_fill_ms"] / 1000
    api_base = _strip_spa_suffix(base_url)
    rest_url = f"{api_base}{rest_endpoint}"
    domain = _extract_cookie_domain(base_url)

    if rest_auth_method == "basic":
        auth_string = base64.b64encode(
            f"{credentials['username']}:{credentials['password']}".encode("utf-8")
        ).decode("utf-8")
        authorization_header = f"Basic {auth_string}"
    elif rest_auth_method == "bearer":
        authorization_header = f"Bearer {credentials['password']}"
    else:
        logger.warning("Unsupported REST auth method: %s", rest_auth_method)
        return None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                rest_url,
                headers={"Authorization": authorization_header},
                timeout=timeout_seconds,
            )

        if response.status_code != 200:
            logger.warning("REST API login returned status %s", response.status_code)
            return None

        response_payload = response.json()
        if response_payload.get("authenticated") is not True:
            logger.warning("REST API login returned authenticated=false")
            return None

        auth_state = {
            "cookies": [
                {
                    "name": cookie_name,
                    "value": cookie_value,
                    "domain": domain,
                    "path": "/",
                }
                for cookie_name, cookie_value in response.cookies.items()
            ],
            "origins": [],
        }

        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(json.dumps(auth_state, indent=2), encoding="utf-8")
        return str(session_file)
    except Exception as error:
        logger.warning("REST API login failed: %s", error)
        return None


async def _login_playwright_form(
    config: dict[str, Any],
    credentials: dict[str, str],
    logger: logging.Logger,
) -> str | None:
    session_file = _session_file(config)
    screenshot_path = _debug_screenshot_path(config)
    auth_config = config["auth"]
    form_config = auth_config["form"]

    headless = auth_config["headless"]
    selector_timeout_ms = auth_config["selector_timeout_ms"]
    page_load_timeout_ms = auth_config["page_load_timeout_ms"]
    wait_before_fill_ms = form_config["wait_before_fill_ms"]
    wait_after_submit_ms = form_config["wait_after_submit_ms"]
    username_selectors = list(form_config["username_selectors"])
    password_selectors = list(form_config["password_selectors"])
    submit_selectors = list(form_config["submit_selectors"])
    forbidden_fragment = _must_not_contain(config)

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_timeout(selector_timeout_ms)

                await page.goto(
                    config["site"]["login_url"],
                    timeout=page_load_timeout_ms,
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(wait_before_fill_ms)

                inputs = []
                for input_element in await page.query_selector_all("input"):
                    inputs.append(
                        {
                            "id": await input_element.get_attribute("id"),
                            "name": await input_element.get_attribute("name"),
                            "type": await input_element.get_attribute("type"),
                            "placeholder": await input_element.get_attribute("placeholder"),
                        }
                    )
                _log_input_elements(logger, inputs)

                await _fill_first_matching_selector(
                    page=page,
                    selectors=username_selectors,
                    value=credentials["username"],
                    selector_timeout_ms=selector_timeout_ms,
                    field_name="username",
                    success_message="✅ Username filled: %s",
                    logger=logger,
                )

                try:
                    await _fill_first_matching_selector(
                        page=page,
                        selectors=password_selectors,
                        value=credentials["password"],
                        selector_timeout_ms=selector_timeout_ms,
                        field_name="password",
                        success_message="✅ Password filled: %s",
                        logger=logger,
                    )
                except RuntimeError:
                    logger.info("↪️ Password field not available yet; advancing login flow after username")
                    await _click_first_matching_selector(
                        page=page,
                        selectors=submit_selectors,
                        selector_timeout_ms=selector_timeout_ms,
                        field_name="submit",
                        success_message="✅ Username step submitted: %s",
                        logger=logger,
                    )
                    await page.wait_for_load_state("domcontentloaded")
                    
                    # Wait longer for multi-step forms (password appears async or on new page)
                    logger.info("🔄 Waiting for password field (multi-step form). Current URL: %s", page.url)
                    retry_count = 3
                    password_filled = False
                    for attempt in range(retry_count):
                        wait_time = wait_before_fill_ms * (2 ** attempt)  # Exponential backoff
                        await page.wait_for_timeout(int(wait_time))
                        
                        # Log available fields for diagnostics
                        inputs_after_step = []
                        for inp in await page.query_selector_all("input"):
                            input_info = {
                                "id": await inp.get_attribute("id"),
                                "name": await inp.get_attribute("name"),
                                "type": await inp.get_attribute("type"),
                                "placeholder": await inp.get_attribute("placeholder"),
                            }
                            inputs_after_step.append(input_info)
                        logger.debug("Attempt %d/%d - Available fields: %s", attempt + 1, retry_count, inputs_after_step)
                        
                        try:
                            await _fill_first_matching_selector(
                                page=page,
                                selectors=password_selectors,
                                value=credentials["password"],
                                selector_timeout_ms=selector_timeout_ms,
                                field_name="password",
                                success_message="✅ Password filled (attempt %d/%d): %%s",
                                logger=logger,
                            )
                            logger.info("✅ Password filled (attempt %d/%d) after %dms wait", attempt + 1, retry_count, wait_time)
                            password_filled = True
                            break
                        except RuntimeError:
                            if attempt == retry_count - 1:
                                logger.warning("⚠️ Password field not found after %d retries; may be on different page", retry_count)
                                raise RuntimeError(f"Unable to locate a password field after {retry_count} retries")
                            continue

                await _click_first_matching_selector(
                    page=page,
                    selectors=submit_selectors,
                    selector_timeout_ms=selector_timeout_ms,
                    field_name="submit",
                    success_message="✅ Submit clicked: %s",
                    logger=logger,
                )

                await page.wait_for_timeout(wait_after_submit_ms)
                try:
                    await page.screenshot(path=str(screenshot_path), timeout=10000)
                except Exception as screenshot_error:
                    logger.warning("Login screenshot skipped: %s", screenshot_error)

                current_url = page.url
                if not _is_login_success_url(current_url, forbidden_fragment):
                    raise RuntimeError(
                        f"Login failed because URL still contains '{forbidden_fragment}': {current_url}"
                    )

                await context.storage_state(path=str(session_file))
                return str(session_file)
            finally:
                await browser.close()
    except Exception as error:
        logger.warning("Playwright form login failed: %s", error)
        return None


async def _fill_first_matching_selector(
    *,
    page,
    selectors: list[str],
    value: str,
    selector_timeout_ms: int,
    field_name: str,
    success_message: str,
    logger: logging.Logger,
) -> None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=selector_timeout_ms)
            await locator.fill(value)
            logger.info(success_message, selector)
            return
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    raise RuntimeError(f"Unable to locate a {field_name} field")


async def _click_first_matching_selector(
    *,
    page,
    selectors: list[str],
    selector_timeout_ms: int,
    field_name: str,
    success_message: str,
    logger: logging.Logger,
) -> None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=selector_timeout_ms)
            await locator.click()
            logger.info(success_message, selector)
            return
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    raise RuntimeError(f"Unable to locate a {field_name} control")


def _resolve_llm_api_key(llm_config: dict[str, Any]) -> str | None:
    if llm_config.get("api_key"):
        return str(llm_config["api_key"])

    api_key_env = llm_config.get("api_key_env")
    if api_key_env:
        return os.getenv(str(api_key_env))

    provider = str(llm_config.get("provider", "")).lower()
    if provider == "groq":
        return os.getenv("GROQ_API_KEY")

    if provider == "github":
        return os.getenv("GITHUB_TOKEN")

    return os.getenv("OPENAI_API_KEY")


def _build_browser_use_llm(config: dict[str, Any]):
    llm_config = config["llm"]
    provider = str(llm_config.get("provider", "openai")).lower()

    if provider == "groq":
        from browser_use import ChatGroq

        api_key = _resolve_llm_api_key(llm_config)
        if not api_key:
            raise ValueError("GROQ_API_KEY is not configured for Browser Use login")

        llm_kwargs: dict[str, Any] = {
            "model": llm_config["model"],
            "api_key": api_key,
            "temperature": llm_config.get("temperature"),
        }

        for key in ("base_url",):
            value = llm_config.get(key)
            if value is not None:
                llm_kwargs[key] = value

        return ChatGroq(**llm_kwargs)

    if provider == "ollama":
        from browser_use import ChatOllama

        llm_kwargs = {"model": llm_config["model"]}

        for key in ("host", "timeout"):
            value = llm_config.get(key)
            if value is not None:
                llm_kwargs[key] = value

        return ChatOllama(**llm_kwargs)

    from browser_use import ChatOpenAI

    api_key = _resolve_llm_api_key(llm_config)
    if not api_key:
        raise ValueError("LLM API key is not configured for Browser Use login")

    llm_kwargs = {
        "model": llm_config["model"],
        "api_key": api_key,
        "temperature": llm_config.get("temperature"),
    }

    optional_keys = ("base_url", "organization", "project")
    for key in optional_keys:
        value = llm_config.get(key)
        if value is not None:
            llm_kwargs[key] = value

    return ChatOpenAI(**llm_kwargs)


async def _login_browser_use_agent(
    config: dict[str, Any],
    credentials: dict[str, str],
    logger: logging.Logger,
) -> str | None:
    from browser_use import Agent, BrowserProfile, BrowserSession

    session_file = _session_file(config)
    screenshot_path = _debug_screenshot_path(config)
    auth_config = config["auth"]
    agent_config = auth_config["agent"]
    forbidden_fragment = _must_not_contain(config)

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    browser_session = BrowserSession(
        browser_profile=BrowserProfile(
            headless=auth_config["headless"],
            keep_alive=True,
            wait_for_network_idle_page_load_time=15,
            minimum_wait_page_load_time=3,
        ),
    )

    try:
        llm = _build_browser_use_llm(config)
        await browser_session.start()
        await browser_session.navigate_to(config["site"]["login_url"])

        task = str(agent_config["task_template"]).format(
            login_url=config["site"]["login_url"],
            username=credentials["username"],
            password=credentials["password"],
        )

        agent = Agent(
            task=task,
            llm=llm,
            browser_session=browser_session,
            max_clickable_elements_length=agent_config["snapshot_max_tokens"],
        )

        await agent.run(max_steps=agent_config["max_steps"])

        current_url = await browser_session.get_current_page_url()
        if not _is_login_success_url(current_url, forbidden_fragment):
            raise RuntimeError(
                f"Browser Use login failed because URL still contains '{forbidden_fragment}': {current_url}"
            )

        # Ensure all portal domain cookies are captured by navigating to base URL
        logger.debug("Navigating to base URL to capture portal session cookies...")
        await browser_session.navigate_to(config["site"]["base_url"])
        await asyncio.sleep(2)  # Wait for session cookies to be set

        current_page = await browser_session.get_current_page()
        if current_page is not None:
            try:
                screenshot_data = await current_page.screenshot(timeout=10000)
                screenshot_path.write_bytes(base64.b64decode(screenshot_data))
            except Exception as screenshot_error:
                logger.warning("Browser Use login screenshot skipped: %s", screenshot_error)

        await browser_session.export_storage_state(session_file)
        logger.info("Session state exported with all domain cookies")
        return str(session_file)
    except Exception as error:
        logger.warning("Browser Use agent login failed: %s", error)
        return None
    finally:
        try:
            await browser_session.kill()
        except Exception:
            pass
