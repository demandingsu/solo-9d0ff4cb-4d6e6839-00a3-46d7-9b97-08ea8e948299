"""北京时间解释、封闭区间生成与区间相交判断。

规则（需求约定）：
- 公告中的所有时间均按北京时间（UTC+8）解释；
- 跨午夜封闭归属“开始当天”，即封闭日按开始钟点所在日期计；
- 生效日期同样限定封闭“开始日”落在生效区间内；
- 通行区间与封闭区间均按半开区间 [start, end) 判断相交。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator, Optional

BEIJING_TZ = timezone(timedelta(hours=8), name="UTC+8")

# 中文星期 -> Python weekday()（周一=0 ... 周日=6）
_WEEKDAY_ALIASES: dict[str, int] = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
    "日": 6, "天": 6, "末": None,  # “周末”单独处理
}
_CN_WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass(frozen=True)
class ClockRange:
    start: time
    end: time
    cross_midnight: bool


def to_beijing(dt: datetime) -> datetime:
    """把任意带时区时间转换为北京时间。"""
    return dt.astimezone(BEIJING_TZ)


def normalize_road_id(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def normalize_direction(value: str) -> str:
    return re.sub(r"\s+", "", value)


def parse_weekdays(text: str) -> list[int]:
    """解析“每周一、周三 / 每周六周日 / 工作日 / 周末”等表述。"""
    if not text:
        raise ValueError("未找到每周封闭日")
    t = text.replace("礼拜", "周").replace("星期", "周")
    result: set[int] = set()

    if "工作日" in t:
        result.update(range(0, 5))
    if "周末" in t:
        result.update((5, 6))

    # 周一 ... 周日 / 周天
    for m in re.finditer(r"周([一二三四五六日天])", t):
        result.add(_WEEKDAY_ALIASES[m.group(1)])

    if not result:
        raise ValueError(f"无法解析封闭日: {text!r}")
    return sorted(result)


_HM_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")


def parse_clock_range(text: str) -> ClockRange:
    """解析起止钟点，识别“次日/翌日”跨午夜表述。

    支持：22:00 至 次日 05:00、08:00-18:00、00:00～24:00。
    跨午夜封闭归属开始当天。
    """
    if not text:
        raise ValueError("未找到起止钟点")
    pairs = _HM_RE.findall(text)
    if len(pairs) < 2:
        raise ValueError(f"无法解析起止钟点: {text!r}")

    def _mk(h: str, m: str) -> time:
        hi, mi = int(h), int(m)
        if hi == 24 and mi == 0:
            return time(0, 0)
        if not (0 <= hi <= 23 and 0 <= mi <= 59):
            raise ValueError(f"非法钟点: {h}:{m}")
        return time(hi, mi)

    start = _mk(*pairs[0])
    end = _mk(*pairs[1])
    explicit_next_day = bool(re.search(r"次日|翌日|第二天", text))
    # 24:00 写法等价于次日 00:00
    explicit_24 = bool(re.search(r"24\s*[:：]\s*00", text))
    cross = explicit_next_day or explicit_24 or end <= start
    return ClockRange(start=start, end=end, cross_midnight=cross)


_EFF_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"(?:\s*[起至到\-—~～]+\s*"
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日)?"
)


def parse_effective_range(text: str) -> tuple[date, Optional[date]]:
    """解析生效日期区间，返回 (开始日, 结束日|None 表示长期有效)。"""
    if not text:
        raise ValueError("未找到生效日期")
    m = _EFF_RE.search(text)
    if not m:
        raise ValueError(f"无法解析生效日期: {text!r}")
    start = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    end: Optional[date] = None
    if m.group(6):
        end_year = int(m.group(4)) if m.group(4) else start.year
        end = date(end_year, int(m.group(5)), int(m.group(6)))
    if end is not None and end < start:
        raise ValueError("生效结束日早于开始日")
    return start, end


def is_intersecting(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """两个半开区间是否相交。"""
    return a_start < b_end and b_start < a_end


def iter_closure_windows(
    *,
    effective_start: date,
    effective_end: Optional[date],
    weekdays: list[int],
    clock: ClockRange,
    travel_enter: datetime,
    travel_exit: datetime,
) -> Iterator[tuple[datetime, datetime]]:
    """枚举与通行区间可能相交的封闭区间。

    封闭区间的开始日必须：
      1) 命中每周封闭日；
      2) 落在生效日期区间内（生效日期限定封闭开始日）。
    跨午夜时结束时刻落在开始日的次日，但归属仍计开始日。
    """
    # 向前多取 1 天，覆盖跨午夜窗口的尾部与通行区间相交的情况
    scan_start = max(effective_start, travel_enter.date() - timedelta(days=1))
    scan_end = travel_exit.date()

    d = scan_start
    weekday_set = set(weekdays)
    while d <= scan_end:
        if d.weekday() in weekday_set and d >= effective_start:
            if effective_end is None or d <= effective_end:
                start_dt = datetime.combine(d, clock.start, BEIJING_TZ)
                end_day = d + timedelta(days=1) if clock.cross_midnight else d
                end_dt = datetime.combine(end_day, clock.end, BEIJING_TZ)
                if is_intersecting(travel_enter, travel_exit, start_dt, end_dt):
                    yield start_dt, end_dt
        d += timedelta(days=1)


def cn_weekday_name(weekday: int) -> str:
    return _CN_WEEKDAY[weekday]
