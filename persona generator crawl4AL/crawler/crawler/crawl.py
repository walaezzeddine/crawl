"""Full site recursive crawl using Crawl4AI with authenticated session."""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig


class SessionExpiredError(Exception):
    """Raised when the authenticated session expires during crawling."""


def _is_internal_link(url: str, base_url: str) -> bool:
    """Return True only when the URL stays within the base domain."""
    return urlparse(url).netloc.lower() == urlparse(base_url).netloc.lower()


def _is_file_url(url: str, excluded_extensions: list) -> bool:
    """Return True when the URL path ends with an excluded file extension."""
    path = urlparse(url).path.lower()
    return any(path.endswith(extension.lower()) for extension in excluded_extensions)


def _looks_like_session_expiry(error: Exception) -> bool:
    """Detect whether a crawl error looks like an expired authenticated session."""
    message = str(error).lower()
    return any(
        indicator in message
        for indicator in ["unauthorized", "forbidden", "session", "login", "401", "403", "auth"]
    )


def _extract_links_from_markdown(markdown: str, base_url: str) -> list:
    """Extract links from markdown content using multiple patterns."""
    extracted = []
    
    if not markdown:
        return extracted
    
    # Pattern 1: [text](url) markdown format
    md_links = re.findall(r"\[.*?\]\((https?://[^\)]+)\)", markdown)
    extracted.extend(md_links)
    
    # Pattern 2: Direct URLs in text
    url_pattern = r"https?://[^\s\)\]\}\"<>]+"
    url_links = re.findall(url_pattern, markdown)
    extracted.extend(url_links)
    
    # Pattern 3: Relative paths like /path or ./path
    relative_pattern = r"(?:^|\s|href=['\"]?|data-href=['\"]?)(/[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*)"
    relative_matches = re.findall(relative_pattern, markdown, re.MULTILINE)
    for rel in relative_matches:
        if rel and rel != "/" and not rel.startswith("/static/"):
            absolute = urljoin(base_url, rel)
            extracted.append(absolute)
    
    # Pattern 4: Look for onclick or data attributes with routes
    # E.g., onclick="navigate('/dashboard')" or data-route="/forms"
    route_pattern = r"(?:navigate|route|href|to)['\"]?\s*\(['\"]?(/[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*)['\"]?"
    route_matches = re.findall(route_pattern, markdown, re.IGNORECASE)
    for route in route_matches:
        if route and route != "/" and not route.startswith("/static/"):
            absolute = urljoin(base_url, route)
            extracted.append(absolute)
    
    return extracted


def _extract_links_from_html_structure(markdown: str, base_url: str) -> list:
    """Extract links from HTML structures that may be in markdown conversion."""
    extracted = []
    
    # Pattern for href in HTML-like strings that made it to markdown
    href_pattern = r"href=['\"]([^'\"]+)['\"]"
    hrefs = re.findall(href_pattern, markdown)
    
    for href in hrefs:
        if href.startswith("http"):
            extracted.append(href)
        elif href.startswith("/"):
            extracted.append(urljoin(base_url, href))
    
    return extracted


async def crawl_full_site(config: dict, auth_state_path: str) -> list:
    """Recursively crawl internal pages starting from the configured base URL."""
    logger = logging.getLogger("authenticated_crawler")

    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        storage_state=auth_state_path,
    )
    run_config = CrawlerRunConfig(
        wait_for="js:() => document.body && document.body.innerText.length > 100",
        session_id=config["session"]["session_id"],
        page_timeout=config["site"]["timeout_per_page"] * 1000,
        js_code="window.scrollTo(0, document.body.scrollHeight);",
        delay_before_return_html=6.0,
    )

    base_url = config["site"]["base_url"]
    max_pages = config["site"]["max_pages"]
    excluded_extensions = config["crawl"]["excluded_extensions"]
    spa_seed_routes = config["site"]["spa_seed_routes"]

    visited = set()
    queue = [base_url]
    results = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        while queue and len(visited) < max_pages:
            url = queue.pop(0)

            if url in visited:
                continue
            if not _is_internal_link(url, base_url):
                continue
            if _is_file_url(url, excluded_extensions):
                continue

            logger.info("🕷️ Crawling (%s/%s): %s", len(visited) + 1, max_pages, url)

            try:
                result = await crawler.arun(url, config=run_config)
                metadata = getattr(result, "metadata", {}) or {}
                links = getattr(result, "links", {}) or {}
                internal_links = links.get("internal", []) if isinstance(links, dict) else []
                external_links = links.get("external", []) if isinstance(links, dict) else []

                page_data = {
                    "url": url,
                    "title": metadata.get("title", ""),
                    "content": getattr(result, "markdown", "") or "",
                    "links": [],
                    "status": "success",
                    "error": None,
                }

                # Extract links from Crawl4AI's native links dict
                for link in [*internal_links, *external_links]:
                    href = link.get("href", "") if isinstance(link, dict) else ""
                    if not href:
                        continue
                    absolute_href = urljoin(url, href)
                    if not _is_internal_link(absolute_href, base_url):
                        continue
                    if absolute_href not in visited:
                        queue.append(absolute_href)
                    if absolute_href not in page_data["links"]:
                        page_data["links"].append(absolute_href)

                # Enhanced extraction: Parse markdown for links
                markdown_links = _extract_links_from_markdown(getattr(result, "markdown", "") or "", base_url)
                for discovered_url in markdown_links:
                    if not _is_internal_link(discovered_url, base_url):
                        continue
                    if discovered_url not in page_data["links"]:
                        page_data["links"].append(discovered_url)
                    if discovered_url not in visited:
                        queue.append(discovered_url)

                # Enhanced extraction: Parse HTML structures from markdown
                html_links = _extract_links_from_html_structure(getattr(result, "markdown", "") or "", base_url)
                for discovered_url in html_links:
                    if not _is_internal_link(discovered_url, base_url):
                        continue
                    if discovered_url not in page_data["links"]:
                        page_data["links"].append(discovered_url)
                    if discovered_url not in visited:
                        queue.append(discovered_url)

                if len(page_data["links"]) == 0 and len(visited) == 0:
                    base = base_url.rstrip("/")
                    for route in spa_seed_routes:
                        seed = f"{base}{route}"
                        if seed not in visited and seed not in queue:
                            queue.append(seed)
                    logger.warning("⚠️ No links found on home page. First page content preview: %s...", 
                                   page_data["content"][:200])
                    logger.warning("⚠️ Injecting SPA seed URLs as fallback: %s", spa_seed_routes)

                results.append(page_data)
                visited.add(url)
                logger.info("✅ Done: %s — %s links found", url, len(page_data["links"]))
            except asyncio.TimeoutError as error:
                logger.warning("⚠️ Failed: %s — %s", url, error)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "links": [],
                        "status": "failed",
                        "error": str(error),
                    }
                )
                visited.add(url)
                continue
            except Exception as error:
                if _looks_like_session_expiry(error):
                    logger.error("❌ Session expired while crawling %s: %s", url, error)
                    raise SessionExpiredError(str(error)) from error

                logger.warning("⚠️ Failed: %s — %s", url, error)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "links": [],
                        "status": "failed",
                        "error": str(error),
                    }
                )
                visited.add(url)
                continue

    logger.info("🏁 Crawl complete: %s pages visited", len(visited))
    return results
