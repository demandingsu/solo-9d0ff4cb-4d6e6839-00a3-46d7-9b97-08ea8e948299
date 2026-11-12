"""公告采集：抓取同站分页列表与全部详情页，失败逐条记录。"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .parser import ClosureAnnouncement, Listing, normalize_url, parse_detail, parse_listing

MAX_PAGES = 50  # 防止分页环路导致的无限抓取
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
RETRIES = 1  # 失败后再尝试 1 次（共 2 次请求）


@dataclass
class FetchFailureRecord:
    url: str
    stage: str
    reason: str


@dataclass
class ScrapeResult:
    announcements: list[ClosureAnnouncement] = field(default_factory=list)
    failures: list[FetchFailureRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


async def _get(client: httpx.AsyncClient, url: str) -> str:
    last_exc: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:  # 含状态码错误、超时、连接错误
            last_exc = exc
    raise last_exc  # type: ignore[misc]


async def scrape(list_url: str) -> ScrapeResult:
    """采集入口：分页列表 -> 去重详情链接 -> 逐页解析详情。"""
    result = ScrapeResult()
    headers = {"User-Agent": "coldchain-dispatch-checker/1.0"}

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, follow_redirects=True, headers=headers
    ) as client:
        detail_urls = await _collect_listing_pages(client, list_url, result)

        for detail_url in detail_urls:
            try:
                html = await _get(client, detail_url)
                result.announcements.append(parse_detail(html, detail_url))
            except Exception as exc:  # 详情失败：记录并继续，绝不静默丢弃
                result.failures.append(
                    FetchFailureRecord(
                        url=detail_url, stage="detail", reason=_reason(exc)
                    )
                )

    return result


async def _collect_listing_pages(
    client: httpx.AsyncClient, first_url: str, result: ScrapeResult
) -> list[str]:
    """遍历同站分页，汇总去重后的详情链接。"""
    collected: list[str] = []
    seen_pages: set[str] = set()
    seen_details: set[str] = set()
    url = normalize_url(first_url)

    for _ in range(MAX_PAGES):
        if url in seen_pages:
            break
        seen_pages.add(url)

        try:
            html = await _get(client, url)
        except Exception as exc:
            result.failures.append(
                FetchFailureRecord(url=url, stage="list", reason=_reason(exc))
            )
            break

        listing: Listing = parse_listing(html, url)
        for link in listing.detail_links:
            key = normalize_url(link)
            if key not in seen_details:
                seen_details.add(key)
                collected.append(link)

        if not listing.next_page_url:
            break
        url = normalize_url(listing.next_page_url)

    return collected


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败"
    return f"{type(exc).__name__}: {exc}"
