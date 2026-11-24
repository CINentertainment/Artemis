from dataclasses import dataclass

import httpx
import ipaddress
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


def normalize_result_url(value):
    """Discard malformed/relative/credential-bearing results; never decode redirect tokens."""
    if not isinstance(value,str):return None
    try:
        parts=urlsplit(value.strip())
        host=parts.hostname
        if parts.scheme!='https' or not host or parts.username is not None or parts.password is not None or parts.port not in (None,443):return None
        if host.casefold() in {'localhost','localhost.localdomain'} or '.' not in host:return None
        try:
            if not ipaddress.ip_address(host).is_global:return None
        except ValueError:pass
        return urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,''))
    except ValueError:return None


def search_web(query: str, *, limit: int = 5, categories: str | None = None, time_range: str | None = None) -> list[SearchResult]:
    query = query.strip()
    if not query:
        raise ValueError("Enter a research topic.")
    if not 1 <= limit <= 20:
        raise ValueError("Search limit must be between 1 and 20.")

    if categories not in (None, "news", "general"):
        raise ValueError("Unsupported search category.")
    if time_range not in (None, "day", "month", "year"):
        raise ValueError("Unsupported search time range.")
    params = {"q": query, "format": "json"}
    if categories:
        params["categories"] = categories
    if time_range:
        params["time_range"] = time_range

    try:
        response = httpx.get(
            "http://127.0.0.1:8888/search",
            params=params,
            timeout=20.0,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(
            f"SearXNG returned HTTP {error.response.status_code}."
        ) from None
    except httpx.RequestError:
        raise RuntimeError(
            "Could not reach SearXNG. Check that it is running in Ubuntu."
        ) from None

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("SearXNG returned invalid JSON.") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise RuntimeError("SearXNG returned an unexpected response.")

    results = []
    seen=set()
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue

        url = normalize_result_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)

        results.append(
            SearchResult(
                title=item.get("title") or url,
                url=url,
                snippet=item.get("content") or "",
                published_at=item.get("publishedDate") if isinstance(item.get("publishedDate"), str) else None,
            )
        )
        if len(results) == limit:
            break

    return results
