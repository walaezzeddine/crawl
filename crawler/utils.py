"""Utility helpers for logging, file saving, and URL filtering."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup


def setup_logger() -> logging.Logger:
    """Configure and return the package logger."""
    logger = logging.getLogger("authenticated_crawler")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)

    return logger


def save_results(results: list, path: str) -> None:
    """Save crawl results as formatted JSON."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    logging.getLogger("authenticated_crawler").info("💾 Results saved to %s", path)


def save_summary(results: list, summary_path: str, results_path: str) -> None:
    """Save a human-readable crawl summary."""
    output_path = Path(summary_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    successful_pages = [result for result in results if result.get("status") == "success"]
    failed_pages = [result for result in results if result.get("status") == "failed"]
    unique_links = {
        link
        for result in results
        for link in result.get("links", [])
        if link
    }

    lines = [
        f"Total pages crawled successfully: {len(successful_pages)}",
        f"Total pages failed: {len(failed_pages)}",
        f"Total unique internal links found: {len(unique_links)}",
        f"Results file: {results_path}",
        "",
        "URL status list:",
    ]

    for result in results:
        lines.append(f"- {result.get('status', 'unknown')}: {result.get('url', '')}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logging.getLogger("authenticated_crawler").info("📋 Summary saved to %s", summary_path)


def is_internal_link(url: str, base_url: str) -> bool:
    """Return True when both URLs share the same domain."""
    parsed_url = urlparse(url)
    parsed_base_url = urlparse(base_url)
    return parsed_url.netloc.lower() == parsed_base_url.netloc.lower()


def is_file_url(url: str, excluded_extensions: list) -> bool:
    """Return True when the URL path ends with an excluded extension."""
    path = urlparse(url).path.lower()
    normalized_extensions = [extension.lower() for extension in excluded_extensions]
    return any(path.endswith(extension) for extension in normalized_extensions)


def detect_login_selectors(html: str) -> dict:
    """Detect login form selectors from HTML (text-only, no vision required).

    Returns a mapping with keys: `username_field`, `password_field`, `submit_button`.
    Values are CSS selectors or None when not found.
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = {
        "username_field": None,
        "password_field": None,
        "submit_button": None,
    }

    # Find username/email input
    for field in soup.find_all("input"):
        input_type = (field.get("type") or "").lower()
        if input_type in ("text", "email", "", "username"):
            combined = (field.get("id", "") + field.get("name", "") + field.get("placeholder", "")).lower()
            if any(kw in combined for kw in ["user", "email", "login", "username"]):
                field_id = field.get("id")
                field_name = field.get("name")
                selectors["username_field"] = f"#{field_id}" if field_id else (f'input[name="{field_name}"]' if field_name else None)
                break

    # Find password input
    password_input = soup.find("input", {"type": "password"})
    if password_input:
        field_id = password_input.get("id")
        field_name = password_input.get("name")
        selectors["password_field"] = f"#{field_id}" if field_id else (f'input[name="{field_name}"]' if field_name else None)

    # Find submit button
    for button in soup.find_all(["button", "input"]):
        b_type = (button.get("type") or "").lower()
        if button.name == "button" or b_type in ("submit", "button", ""):
            button_text = ((button.get_text() or "") + button.get("value", "")).lower()
            if any(kw in button_text for kw in ["login", "sign in", "sign-in", "submit", "enter"]):
                button_id = button.get("id")
                button_name = button.get("name")
                if button_id:
                    selectors["submit_button"] = f"#{button_id}"
                elif button_name and button.name == "button":
                    selectors["submit_button"] = f'button[name="{button_name}"]'
                else:
                    selectors["submit_button"] = "button[type=submit]"
                break

    logging.getLogger("authenticated_crawler").debug("Detected selectors: %s", selectors)
    return selectors