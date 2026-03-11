from typing import Optional
import aiohttp
from loguru import logger


BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )
    return _session


async def close_brave_session():
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def search_brave(api_key: str, query: str, count: int = 5) -> Optional[str]:
    """Ищет в Brave Search. Возвращает форматированный текст."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": query,
        "count": count,
        "search_lang": "ru",
        "country": "ru",
        "safesearch": "moderate",
        "freshness": "py",
    }

    try:
        session = await _get_session()
        async with session.get(
            BRAVE_SEARCH_URL,
            headers=headers,
            params=params,
        ) as resp:
            if resp.status != 200:
                logger.warning("Brave Search HTTP {}: {}", resp.status, await resp.text())
                return None
            data = await resp.json()
    except Exception as e:
        logger.warning("Brave Search ошибка: {}", e)
        return None

    results = data.get("web", {}).get("results", [])
    if not results:
        return None

    parts = []
    for i, r in enumerate(results[:count], 1):
        title = r.get("title", "")
        description = r.get("description", "")
        url = r.get("url", "")
        parts.append(f"[{i}] {title}\n{description}\nИсточник: {url}")

    return "\n\n".join(parts)
