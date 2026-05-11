"""Orchestrator: loads config, runs login agent, launches crawl, saves output."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from crawler import auth, crawl
from crawler.utils import save_results, save_summary, setup_logger


def _load_config() -> dict:
    """Load the YAML configuration from the project root."""
    config_path = Path(__file__).resolve().parent / "config.yaml"
    with config_path.open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def _build_credentials() -> dict:
    """Build credentials from environment variables."""
    username = os.getenv("SITE_USERNAME")
    password = os.getenv("SITE_PASSWORD")

    if not username or not password:
        raise ValueError("SITE_USERNAME and SITE_PASSWORD must be set in .env")

    return {"username": username, "password": password}


async def main() -> None:
    """Run the authenticated crawl pipeline."""
    load_dotenv()
    config = _load_config()
    credentials = _build_credentials()

    output_folder = Path(config["output"]["folder"])
    output_folder.mkdir(parents=True, exist_ok=True)

    logger = setup_logger()
    logger.info("🚀 Starting authenticated crawler for %s", config["site"]["base_url"])

    auth_state_path = await auth.get_or_create_session(config, credentials)

    try:
        results = await crawl.crawl_full_site(config, auth_state_path)
    except crawl.SessionExpiredError:
        logger.error("❌ Session expired mid-crawl, re-authenticating and retrying once")
        auth_state_path = await auth.get_or_create_session(config, credentials)
        results = await crawl.crawl_full_site(config, auth_state_path)

    save_results(results, config["output"]["results_file"])
    save_summary(results, config["output"]["summary_file"], config["output"]["results_file"])

    success_count = len([result for result in results if result["status"] == "success"])
    failed_count = len([result for result in results if result["status"] == "failed"])

    logger.info("✅ Done! %s pages crawled, %s failed.", success_count, failed_count)
    logger.info("📁 Results saved to %s/", config["output"]["folder"])


if __name__ == "__main__":
    asyncio.run(main())