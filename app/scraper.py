"""公告采集：抓取同站分页列表与全部详情页，失败逐条记录。"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .parser import Listing, normalize_url, parse_detail, parse_listing

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
    # 分页异常标记：出现任一情况都意味着采集不完整，不得用于基线比较
    pagination_loop: bool = False  # “下一页”指向已访问页面，构成分页环路
    page_limit_hit: bool = False  # 达到 MAX_PAGES 上限仍有后续页面
    pages_fetched: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.failures
            and not self.pagination_loop
            and not self.page_limit_hit
        )


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
    """遍历同站分页，汇总去重后的详情链接。

    三种非正常结束均显式标记，调用方据此拒绝把漏采当作公告撤下：
    - 列表页请求失败：记入 failures 后停止；
    - 分页环路：下一页指向已访问页面，置 pagination_loop 并记录问题地址；
    - 页数上限：达到 MAX_PAGES 仍有下一页，置 page_limit_hit 并记录末页地址。
    """
    collected: list[str] = []
    seen_pages: set[str] = set()
    seen_details: set[str] = set()
    url = normalize_url(first_url)

    for _ in range(MAX_PAGES):
        if url in seen_pages:
            # 入口 URL 自身重复（防御性处理，正常流程由下方 next 判断覆盖）
            result.pagination_loop = True
            result.failures.append(
                FetchFailureRecord(
                    url=url, stage="list", reason="分页环路：列表页重复出现"
                )
            )
            break
        seen_pages.add(url)

        try:
            html = await _get(client, url)
        except Exception as exc:
            result.failures.append(
                FetchFailureRecord(url=url, stage="list", reason=_reason(exc))
            )
            break
        result.pages_fetched += 1

        listing: Listing = parse_listing(html, url)
        for link in listing.detail_links:
            key = normalize_url(link)
            if key not in seen_details:
                seen_details.add(key)
                collected.append(link)

        if not listing.next_page_url:
            break
        next_url = normalize_url(listing.next_page_url)
        if next_url in seen_pages:
            # 分页环路：下一页回到已采集页，完整集合不可信
            result.pagination_loop = True
            result.failures.append(
                FetchFailureRecord(
                    url=next_url, stage="list", reason="分页环路：下一页指向已采集页面"
                )
            )
            break
        url = next_url
    else:
        # for 正常耗尽即跑满 MAX_PAGES：仍可能存在未采集的后续页面
        result.page_limit_hit = True
        result.failures.append(
            FetchFailureRecord(
                url=url,
                stage="list",
                reason=f"达到分页页数上限 {MAX_PAGES}，后续页面未采集",
            )
        )

    return collected


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败"
    return f"{type(exc).__name__}: {exc}"
