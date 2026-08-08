"""
Notice-board scraper — async version of the original `scrape_notices()`.

Fetches https://maldacollege.ac.in/home.php and extracts notices from the
<div id="notice" class="notice_content"> section. Each notice dict carries:
  - title    : str  (the bold text, e.g. "05 Aug: Notice about ...")
  - link     : str  (the "Click here" URL)
  - slide_id : str | None
"""

from __future__ import annotations

import re

import aiohttp
from bs4 import BeautifulSoup

import config


async def scrape_notices() -> list[dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                config.TARGET_URL, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
    except Exception as exc:  # noqa: BLE001
        config.logger.error("scrape_notices fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    notice_div = soup.find("div", id="notice", class_="notice_content")
    if not notice_div:
        config.logger.warning("Could not locate <div id='notice'> on the page.")
        return []

    notices: list[dict] = []
    for matter in notice_div.find_all("div", class_="notice_matter"):
        strong = matter.find("strong")
        if not strong:
            continue
        title = strong.get_text(strip=True)
        link = ""
        slide_id = None
        for anchor in matter.find_all("a", href=True):
            link_text = anchor.get_text(strip=True).lower()
            if "click here" in link_text or "click" in link_text:
                link = anchor["href"]
                match = re.search(r"slide=(id\.[^&]+)", link)
                if match:
                    slide_id = match.group(1)
                break
        if title and link:
            notices.append({"title": title, "link": link, "slide_id": slide_id})

    config.logger.info("Scraped %d notices from the page.", len(notices))
    return notices
