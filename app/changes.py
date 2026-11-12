"""公告变更检测：完整采集 -> 与 SQLite 基线比较 -> 原子替换基线。

规则（需求约定）：
- 以公告详情 URL 标识同一条公告；
- 比较字段：路段、方向、生效日期、每周封闭日、起止钟点；
- **首次完整采集只建立基线**，不产出任何新增/撤下/修改；
- 仅当同站分页与全部详情采集成功，且未遇分页环路或页数上限时，
  才进行比较并原子替换基线；
- 采集不完整时返回 undetermined 与问题地址，**保留旧基线，
  绝不把漏采当成撤下**。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .models import (
    AnnouncementSnapshot,
    ChangedAnnouncement,
    ChangeResponse,
    FetchFailure,
)
from .parser import normalize_url
from .scraper import scrape
from .storage import (
    COMPARED_FIELDS,
    load_baseline,
    replace_baseline,
    snapshot,
)

@dataclass
class Diff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)


def diff_baseline(old_entries: list[dict], new_entries: list[dict]) -> Diff:
    """按详情 URL 比较新旧两套公告快照。"""
    old_map = {e["detail_url"]: e for e in old_entries}
    new_map = {e["detail_url"]: e for e in new_entries}
    result = Diff()

    for url, entry in new_map.items():
        if url not in old_map:
            result.added.append(entry)
            continue
        old_entry = old_map[url]
        changed_fields: dict[str, dict] = {}
        for name in COMPARED_FIELDS:
            if old_entry.get(name) != entry.get(name):
                changed_fields[name] = {
                    "old": old_entry.get(name),
                    "new": entry.get(name),
                }
        if changed_fields:
            result.changed.append(
                {
                    "detail_url": url,
                    "changed_fields": changed_fields,
                    "old": old_entry,
                    "new": entry,
                }
            )

    for url, entry in old_map.items():
        if url not in new_map:
            result.removed.append(entry)

    return result


def _to_snapshot_model(entry: dict) -> AnnouncementSnapshot:
    return AnnouncementSnapshot(**entry)


async def detect_changes(list_url: str) -> ChangeResponse:
    """变更检测入口：采集、判定完整性、比较、替换基线。"""
    canonical_url = normalize_url(list_url)
    scrape_result = await scrape(canonical_url)

    failures = [
        FetchFailure(url=f.url, stage=f.stage, reason=f.reason)
        for f in scrape_result.failures
    ]

    # 采集不完整（请求失败 / 分页环路 / 页数上限）：
    # 不比较、不替换，保留旧基线，漏采绝不计为撤下
    if not scrape_result.ok:
        reasons = []
        if scrape_result.pagination_loop:
            reasons.append("检测到分页环路")
        if scrape_result.page_limit_hit:
            reasons.append("达到分页页数上限")
        if scrape_result.failures:
            reasons.append(f"{len(scrape_result.failures)} 个地址采集失败")
        return ChangeResponse(
            status="undetermined",
            announcement_list_url=canonical_url,
            complete=False,
            pagination_loop=scrape_result.pagination_loop,
            page_limit_hit=scrape_result.page_limit_hit,
            pages_fetched=scrape_result.pages_fetched,
            fetched_announcements=len(scrape_result.announcements),
            failures=failures,
            message=(
                "本次采集不完整（" + "、".join(reasons) +
                "），无法判定公告变更；已保留旧基线，漏采不计为撤下。"
            ),
        )

    new_entries = [snapshot(a) for a in scrape_result.announcements]

    # SQLite 调用放到线程池，避免阻塞事件循环
    old_entries = await asyncio.to_thread(load_baseline, canonical_url)

    if old_entries is None:
        # 首次完整采集：只建立基线
        meta = await asyncio.to_thread(replace_baseline, canonical_url, new_entries)
        return ChangeResponse(
            status="baseline_created",
            announcement_list_url=canonical_url,
            complete=True,
            pages_fetched=scrape_result.pages_fetched,
            fetched_announcements=len(new_entries),
            baseline_created_at=meta["created_at"],
            baseline_updated_at=meta["updated_at"],
            message=(
                f"首次完整采集成功，已建立基线（{len(new_entries)} 条公告），"
                "本次不产出变更。"
            ),
        )

    diff = diff_baseline(old_entries, new_entries)
    has_change = any((diff.added, diff.removed, diff.changed))

    # 比较完成后原子替换基线（失败会抛异常，旧基线由事务保证保留）
    meta = await asyncio.to_thread(replace_baseline, canonical_url, new_entries)

    if not has_change:
        return ChangeResponse(
            status="unchanged",
            announcement_list_url=canonical_url,
            complete=True,
            pages_fetched=scrape_result.pages_fetched,
            fetched_announcements=len(new_entries),
            baseline_created_at=meta["created_at"],
            baseline_updated_at=meta["updated_at"],
            message=f"完整采集 {len(new_entries)} 条公告，与基线一致，无变更。",
        )

    return ChangeResponse(
        status="changed",
        announcement_list_url=canonical_url,
        complete=True,
        pages_fetched=scrape_result.pages_fetched,
        fetched_announcements=len(new_entries),
        baseline_created_at=meta["created_at"],
        baseline_updated_at=meta["updated_at"],
        added=[_to_snapshot_model(e) for e in diff.added],
        removed=[_to_snapshot_model(e) for e in diff.removed],
        changed=[
            ChangedAnnouncement(
                detail_url=item["detail_url"],
                changed_fields=item["changed_fields"],
                old=_to_snapshot_model(item["old"]),
                new=_to_snapshot_model(item["new"]),
            )
            for item in diff.changed
        ],
        message=(
            f"发现变更：新增 {len(diff.added)} 条、撤下 {len(diff.removed)} 条、"
            f"修改 {len(diff.changed)} 条；基线已原子替换。"
        ),
    )
