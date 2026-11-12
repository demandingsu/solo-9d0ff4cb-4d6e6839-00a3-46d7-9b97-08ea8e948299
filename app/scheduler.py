"""调度核对：按路段（编号+方向）匹配公告，判断通行/封闭区间是否相交。"""
from __future__ import annotations

from .models import (
    BlockedResult,
    BlockingInterval,
    CheckResponse,
    ClearResult,
    FetchFailure,
    RoadSegment,
)
from .parser import ClosureAnnouncement
from .scraper import ScrapeResult
from .timeutils import (
    is_intersecting,
    iter_closure_windows,
    normalize_direction,
    normalize_road_id,
    to_beijing,
)


def build_response(
    segments: list[RoadSegment], scrape_result: ScrapeResult
) -> CheckResponse:
    blocked: list[BlockedResult] = []
    clear: list[ClearResult] = []
    has_failure = bool(scrape_result.failures)

    # 按归一化路段编号建索引
    index: dict[str, list[ClosureAnnouncement]] = {}
    for ann in scrape_result.announcements:
        index.setdefault(normalize_road_id(ann.road_id), []).append(ann)

    for seg in segments:
        enter_bj = to_beijing(seg.enter_at)
        exit_bj = to_beijing(seg.exit_at)
        hit_intervals: list[tuple] = []  # (start, end, url)
        hit_urls: list[str] = []

        for ann in index.get(normalize_road_id(seg.road_id), []):
            if not _direction_matches(seg.direction, ann.direction):
                continue
            for start, end in iter_closure_windows(
                effective_start=ann.effective_start,
                effective_end=ann.effective_end,
                weekdays=ann.weekdays,
                clock=ann.clock,
                travel_enter=enter_bj,
                travel_exit=exit_bj,
            ):
                if is_intersecting(enter_bj, exit_bj, start, end):
                    hit_intervals.append((start, end, ann.url))
                    if ann.url not in hit_urls:
                        hit_urls.append(ann.url)

        if hit_intervals:
            hit_intervals.sort(key=lambda x: x[0])
            blocked.append(
                BlockedResult(
                    road_id=seg.road_id,
                    direction=seg.direction,
                    enter_at=enter_bj,
                    exit_at=exit_bj,
                    blocking=[
                        BlockingInterval(start=s, end=e) for s, e, _ in hit_intervals
                    ],
                    announcements=hit_urls,
                )
            )
        elif not has_failure:
            # 只有全部采集成功时才允许声明“畅通”
            clear.append(
                ClearResult(
                    road_id=seg.road_id,
                    direction=seg.direction,
                    enter_at=enter_bj,
                    exit_at=exit_bj,
                )
            )

    failures = [
        FetchFailure(url=f.url, stage=f.stage, reason=f.reason)
        for f in scrape_result.failures
    ]

    if has_failure:
        status = "undetermined"
        message = (
            f"{len(failures)} 个地址采集失败，相关路段无法判定；"
            "未声明全程畅通。已确认受阻的路段见 blocked。"
        )
    elif blocked:
        status = "blocked"
        message = f"{len(blocked)} 个路段与施工封闭区间相交，需调整计划路线。"
    else:
        status = "clear"
        message = "全部公告采集成功，计划路段未与任何封闭区间相交。"

    return CheckResponse(
        status=status,
        fetched_announcements=len(scrape_result.announcements),
        blocked=blocked,
        clear=clear,
        failures=failures,
        message=message,
    )


def _direction_matches(plan_direction: str, announcement_direction: str) -> bool:
    plan = normalize_direction(plan_direction)
    ann = normalize_direction(announcement_direction)
    if ann == "双向":
        return True
    return plan == ann
