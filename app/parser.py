"""BeautifulSoup 解析：公告列表页（同站分页）与公告详情页。

解析器对页面结构保持适度宽松：优先按语义标签/表格提取，
文本字段通过关键词正则提取，便于适配同构公告页面。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .timeutils import (
    ClockRange,
    parse_clock_range,
    parse_effective_range,
    parse_weekdays,
)

NEXT_PAGE_TOKENS = ("下一页", "下页", "next", ">", "›", "»")


@dataclass
class ClosureAnnouncement:
    """从单条公告详情提取的结构化封闭信息。"""

    url: str
    title: str
    road_id: str
    direction: str
    effective_start: date
    effective_end: date | None
    weekdays: list[int]
    clock: ClockRange
    raw_effective: str = ""
    raw_weekdays: str = ""
    raw_clock: str = ""


@dataclass
class Listing:
    detail_links: list[str] = field(default_factory=list)
    next_page_url: str | None = None


def _absolute(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def _same_origin(base_url: str, target: str) -> bool:
    b, t = urlparse(base_url), urlparse(target)
    return (b.scheme, b.netloc) == (t.scheme, t.netloc)


def parse_listing(html: str, page_url: str) -> Listing:
    """解析列表页：提取同站详情链接与“下一页”链接。"""
    soup = BeautifulSoup(html, "html.parser")
    listing = Listing()
    seen: set[str] = set()

    # 详情链接：优先表格行内链接，其次公告列表容器，最后按 href 关键词兜底
    containers = soup.select("table, ul.announcement-list, div.list, div.announcements")
    anchors = []
    if containers:
        for c in containers:
            anchors.extend(c.find_all("a", href=True))
    if not anchors:
        anchors = soup.find_all("a", href=True)

    for a in anchors:
        href = a.get("href", "").strip()
        if not href or href.startswith("#"):
            continue
        # 排除分页/导航链接（rel=next/prev、分页文本、page=N 查询参数）
        rel = {r.lower() for r in a.get("rel", [])}
        label = a.get_text(" ", strip=True)
        if rel & {"next", "prev"} or _is_pagination_label(label) or _has_page_param(href):
            continue
        absolute = _absolute(page_url, href)
        if not _same_origin(page_url, absolute):
            continue
        # 仅保留看起来像公告详情的链接（带 detail/id/公告号 等特征）
        if not _looks_like_detail(absolute, label):
            continue
        if absolute not in seen:
            seen.add(absolute)
            listing.detail_links.append(absolute)

    # 下一页：按链接文本识别，且必须仍在同站
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        if any(tok.lower() in text for tok in NEXT_PAGE_TOKENS):
            nxt = _absolute(page_url, a["href"].strip())
            if _same_origin(page_url, nxt) and nxt != page_url:
                listing.next_page_url = nxt
                break

    return listing


_DETAIL_HINT = re.compile(r"detail|/id/|notice|announcement|gonggao|gg[_\-]?\d+", re.I)
_PAGE_PARAM_RE = re.compile(r"[?&]page(?:[_=]|num)?\s*=", re.I)
_PAGE_LABELS = ("上一页", "下一页", "下页", "首页", "末页", "尾页", "更多", "next", "prev", "previous")


def _has_page_param(href: str) -> bool:
    return bool(_PAGE_PARAM_RE.search(href))


def _is_pagination_label(label: str) -> bool:
    low = label.strip().lower()
    if not low:
        return False
    if any(tok in low for tok in _PAGE_LABELS):
        return True
    # 纯数字（页码）或符号页码
    return bool(re.fullmatch(r"[0-9]+|[<>›»«‹]+", low))


def _looks_like_detail(url: str, label: str) -> bool:
    path = urlparse(url).path
    if _DETAIL_HINT.search(url):
        return True
    if re.search(r"\d{3,}", path) and label:
        return True
    if "施工" in label or "封闭" in label or "公告" in label:
        return True
    return False


# ---- 详情页字段提取 -------------------------------------------------------

_FIELD_PATTERNS = {
    "road_id": re.compile(r"路段[编号名称]*\s*[:：]?\s*([A-Za-z0-9\-—_]+)"),
    "direction": re.compile(r"方\s*向\s*[:：]?\s*([上下]行|双向)"),
}


def _field_lines(text: str) -> list[str]:
    # 按换行与中文分号切分，保留含关键词的片段
    parts = re.split(r"[\n\r；;]+", text)
    return [p.strip() for p in parts if p.strip()]


def parse_detail(html: str, page_url: str) -> ClosureAnnouncement:
    """解析公告详情，提取路段、方向、生效日期、每周封闭日与起止钟点。"""
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(" ", strip=True) if title_tag else ""

    # 优先取正文容器，取不到则整页文本
    body = soup.select_one("div.article, div.content, div.detail, article")
    container = body if body else soup
    text = container.get_text("\n", strip=True)
    lines = _field_lines(text)
    joined = "\n".join(lines)

    road_id = _extract(lines, _FIELD_PATTERNS["road_id"])
    if not road_id:
        raise ValueError("公告缺少路段编号")

    direction = _extract(lines, _FIELD_PATTERNS["direction"])
    if not direction:
        raise ValueError("公告缺少方向")

    # 生效日期：取“生效/实施/管制”所在行，否则在全文中匹配
    effective_line = _find_line(lines, ("生效", "实施", "管制时间", "施工时间")) or joined
    raw_effective = effective_line
    effective_start, effective_end = parse_effective_range(effective_line)

    weekday_line = _find_line(lines, ("每周", "封闭日", "星期"))
    if not weekday_line:
        raise ValueError("公告缺少每周封闭日")
    weekdays = parse_weekdays(weekday_line)

    # 注意：不能用“至”等通用词，避免先命中“生效日期：…至…”行
    clock_line = _find_line(lines, ("封闭时间", "起止钟点", "起止时间"))
    if not clock_line:
        # 兜底：取含两个钟点的行
        clock_line = next(
            (ln for ln in lines if len(re.findall(r"\d{1,2}\s*[:：]\s*\d{2}", ln)) >= 2),
            None,
        )
    clock = parse_clock_range(clock_line or joined)

    return ClosureAnnouncement(
        url=page_url,
        title=title,
        road_id=road_id.strip(),
        direction=direction.strip(),
        effective_start=effective_start,
        effective_end=effective_end,
        weekdays=weekdays,
        clock=clock,
        raw_effective=raw_effective,
        raw_weekdays=weekday_line,
        raw_clock=clock_line or "",
    )


def _find_line(lines: list[str], keywords: tuple[str, ...]) -> str | None:
    for line in lines:
        if any(k in line for k in keywords):
            return line
    return None


def _extract(lines: list[str], pattern: re.Pattern) -> str | None:
    for line in lines:
        m = pattern.search(line)
        if m:
            return m.group(1).strip()
    m = pattern.search("\n".join(lines))
    return m.group(1).strip() if m else None


def normalize_url(url: str) -> str:
    """去除 fragment，便于分页去重。"""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))
