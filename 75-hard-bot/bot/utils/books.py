"""Book cover fetcher — tries Google Books, falls back to Open Library."""

import httpx
import logging

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"


async def _try_google_books(client: httpx.AsyncClient, title: str) -> str | None:
    resp = await client.get(GOOGLE_BOOKS_API, params={"q": title, "maxResults": 1})
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None
    image_links = items[0].get("volumeInfo", {}).get("imageLinks", {})
    url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if url and url.startswith("http://"):
        url = url.replace("http://", "https://", 1)
    return url


async def _try_open_library(client: httpx.AsyncClient, title: str) -> str | None:
    resp = await client.get(OPEN_LIBRARY_SEARCH, params={"title": title, "limit": 1, "fields": "cover_i"})
    resp.raise_for_status()
    data = resp.json()
    docs = data.get("docs", [])
    if not docs or not docs[0].get("cover_i"):
        return None
    cover_id = docs[0]["cover_i"]
    return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"


async def fetch_book_cover(title: str) -> str | None:
    """Search for a book cover. Never raises — returns None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Try Google Books first
            try:
                url = await _try_google_books(client, title)
                if url:
                    return url
            except Exception:
                pass

            # Fall back to Open Library
            try:
                url = await _try_open_library(client, title)
                if url:
                    return url
            except Exception:
                pass

            return None
    except Exception as e:
        logger.warning("Failed to fetch book cover for '%s': %s", title, e)
        return None
