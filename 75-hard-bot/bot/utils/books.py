"""Book cover fetcher — uses iTunes API (free, no auth, reliable)."""

import httpx
import logging

logger = logging.getLogger(__name__)

ITUNES_SEARCH = "https://itunes.apple.com/search"


async def fetch_book_cover(title: str) -> str | None:
    """Search iTunes for a book and return a high-res cover URL, or None."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                ITUNES_SEARCH,
                params={"term": title, "entity": "ebook", "limit": 1},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return None

            art_url = results[0].get("artworkUrl100")
            if not art_url:
                return None

            # Upscale from 100px to 600px
            return art_url.replace("100x100", "600x600")
    except Exception as e:
        logger.warning("Failed to fetch book cover for '%s': %s", title, e)
        return None
