"""
Notice-board scraper — async version of the original `scrape_notices()`.

Fetches https://maldacollege.ac.in/home.php and extracts notices from the
<div id="notice" class="notice_content"> section. Each notice dict carries:
  - title    : str  (the bold text, e.g. "05 Aug: Notice about ...")
  - link     : str  (the "Click here" URL)
  - slide_id : str | None

Also provides `download_notice_file()`, which resolves a notice's "Click
Here" link (a Google Slides URL pointing at one specific slide) into the
actual file bytes for that slide/notice, so the bot can send the notice
content itself instead of just a link.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import aiohttp
from bs4 import BeautifulSoup

import config

# Browser-like UA — Google occasionally serves a stripped-down page to
# obvious non-browser clients.
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}


async def scrape_notices() -> list[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                config.TARGET_URL, headers=_UA,
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


# ---------------------------------------------------------------------------
# Download the actual notice content (not just the link)
# ---------------------------------------------------------------------------
#
# Every "Click Here" link points at ONE slide inside a Google Slides deck,
# in one of two URL shapes:
#
#   Direct doc id:
#     https://docs.google.com/presentation/d/<DOC_ID>/present?slide=id.<PAGE_ID>
#
#   "Published to web" token:
#     https://docs.google.com/presentation/d/e/<PUB_ID>/pub?...&slide=id.<PAGE_ID>
#
# Google exposes an (unofficial but widely-used) per-slide image export:
#     https://docs.google.com/presentation/d/<DOC_ID_OR_PUB_ID>/export/png
#         ?id=<DOC_ID_OR_PUB_ID>&pageid=<PAGE_ID>
#
# This works for both shapes as long as the deck is publicly viewable
# (which it must be, since the college links it from a public page). If
# Google ever stops honouring the pub-token form for /export/, we fall
# back to downloading the whole published deck as a PDF so the user still
# gets the real document instead of a bare link.


def _parse_slide_link(link: str) -> tuple[str, str, bool] | None:
    """
    Return (doc_id, page_id, is_pub_token) for a Google Slides "Click Here"
    link, or None if the link isn't a recognisable Slides slide link.
    """
    parsed = urlparse(link)
    if "docs.google.com" not in parsed.netloc or "/presentation/" not in parsed.path:
        return None

    qs = parse_qs(parsed.query)
    slide_param = qs.get("slide", [None])[0]
    if not slide_param:
        return None
    page_id = slide_param.split("id.", 1)[-1] if slide_param.startswith("id.") else slide_param
    if not page_id:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    # parts looks like ['presentation', 'd', 'e', '<PUB_ID>', 'pub']  (pub token)
    #                or ['presentation', 'd', '<DOC_ID>', 'present']  (direct id)
    try:
        d_index = parts.index("d")
    except ValueError:
        return None

    if d_index + 1 < len(parts) and parts[d_index + 1] == "e":
        if d_index + 2 >= len(parts):
            return None
        return parts[d_index + 2], page_id, True
    if d_index + 1 >= len(parts):
        return None
    return parts[d_index + 1], page_id, False


async def download_notice_file(
    link: str, session: aiohttp.ClientSession | None = None
) -> tuple[bytes, str, str] | None:
    """
    Resolve a notice's "Click Here" link to actual file bytes.

    Returns (content_bytes, filename, mime_type) on success, or None if the
    content couldn't be downloaded (caller should then fall back to sending
    the plain title + link, exactly like before).
    """
    ref = _parse_slide_link(link)
    if ref is None:
        config.logger.warning("Notice link isn't a recognisable Slides URL: %s", link)
        return None
    doc_id, page_id, is_pub = ref

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        base = f"https://docs.google.com/presentation/d/{'e/' if is_pub else ''}{doc_id}"

        # 1) Try exporting just the one slide as a PNG image (best case —
        #    the user gets exactly the notice, nothing else).
        png_url = f"{base}/export/png?id={doc_id}&pageid={page_id}"
        try:
            async with session.get(
                png_url, headers=_UA, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if resp.status == 200 and ctype.startswith("image/"):
                    data = await resp.read()
                    if data:
                        return data, "notice.png", "image/png"
        except Exception as exc:  # noqa: BLE001
            config.logger.debug("Slide PNG export failed for %s: %s", link, exc)

        # 2) Fall back to the whole published deck as a PDF, so the user at
        #    least gets a real document instead of just a link. Only works
        #    for the pub-token shape (Google doesn't expose PDF export on
        #    the raw editor doc-id without auth).
        if is_pub:
            pdf_url = f"{base}/pub?output=pdf"
            try:
                async with session.get(
                    pdf_url, headers=_UA, timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    if resp.status == 200 and "pdf" in ctype.lower():
                        data = await resp.read()
                        if data:
                            return data, "notice.pdf", "application/pdf"
            except Exception as exc:  # noqa: BLE001
                config.logger.debug("Deck PDF export failed for %s: %s", link, exc)

        return None
    finally:
        if own_session:
            await session.close()
