from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def search_web(query: str, *, limit: int = 5) -> list[SearchResult]:
    query = query.strip()
    if not query:
        raise ValueError("Enter a research topic.")
    if not 1 <= limit <= 20:
        raise ValueError("Search limit must be between 1 and 20.")

    try:
        response = httpx.get(
            "http://127.0.0.1:8888/search",
            params={"q": query, "format": "json"},
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
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue

        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            continue

        results.append(
            SearchResult(
                title=item.get("title") or url,
                url=url,
                snippet=item.get("content") or "",
            )
        )
        if len(results) == limit:
            break

    return results