"""Book cover fetcher — uses iTunes API (free, no auth, reliable)."""

import httpx
import logging

logger = logging.getLogger(__name__)

ITUNES_SEARCH = "https://itunes.apple.com/search"


def _upscale(art_url: str | None) -> str | None:
    if not art_url:
        return None
    return art_url.replace("100x100", "600x600")


async def search_books(query: str, limit: int = 3) -> list[dict]:
    """Search iTunes for books. Returns list of {title, author, cover_url}.

    Empty list on failure or no matches.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                ITUNES_SEARCH,
                params={"term": query, "entity": "ebook", "limit": limit},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
    except Exception as e:
        logger.warning("Book search failed for '%s': %s", query, e)
        return []

    out: list[dict] = []
    for r in results[:limit]:
        out.append({
            "title": r.get("trackName") or "",
            "author": r.get("artistName") or "",
            "cover_url": _upscale(r.get("artworkUrl100")) or "",
        })
    return out


async def fetch_book_cover(title: str) -> str | None:
    """Backwards-compatible single-cover fetch. Returns first match's URL or None."""
    results = await search_books(title, limit=1)
    return results[0]["cover_url"] if results and results[0]["cover_url"] else None
