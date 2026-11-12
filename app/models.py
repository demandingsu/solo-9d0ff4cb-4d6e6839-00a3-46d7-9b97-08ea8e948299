"""Pydantic 模型：请求路段与响应结构。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 整体判定状态：
# - clear          全部公告均成功采集，且所有路段均无相交封闭
# - blocked        存在受阻路段（不代表全部路段受阻）
# - undetermined   存在采集失败，无法给出完整判定（绝不会降级为“畅通”）
CheckStatus = Literal["clear", "blocked", "undetermined"]


class RoadSegment(BaseModel):
    """调度计划中的一个路段通行区间。"""

    road_id: str = Field(..., description="路段编号，如 G15-K123")
    direction: str = Field(..., description="通行方向，如 上行/下行；与公告方向做归一化匹配")
    enter_at: datetime = Field(..., description="带时区的驶入时间（ISO 8601）")
    exit_at: datetime = Field(..., description="带时区的驶出时间（ISO 8601）")

    @field_validator("enter_at", "exit_at")
    @classmethod
    def _must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("时间必须带时区，例如 2026-11-16T21:00:00+08:00")
        return v

    @field_validator("exit_at")
    @classmethod
    def _exit_after_enter(cls, v: datetime, info) -> datetime:
        enter = info.data.get("enter_at")
        if enter is not None and v <= enter:
            raise ValueError("驶出时间必须晚于驶入时间")
        return v


class CheckRequest(BaseModel):
    """采集与核对请求。"""

    announcement_list_url: str = Field(..., description="公告列表页 URL")
    segments: list[RoadSegment] = Field(..., min_length=1)


class BlockingInterval(BaseModel):
    """与通行区间相交的一段封闭区间（北京时间，带 +08:00 偏移）。"""

    start: datetime
    end: datetime


class BlockedResult(BaseModel):
    """单个受阻路段的命中明细。"""

    road_id: str
    direction: str
    enter_at: datetime
    exit_at: datetime
    blocking: list[BlockingInterval]
    announcements: list[str] = Field(
        default_factory=list, description="命中的公告详情页链接（去重）"
    )


class ClearResult(BaseModel):
    """未命中封闭的路段（仅在全部采集成功时给出）。"""

    road_id: str
    direction: str
    enter_at: datetime
    exit_at: datetime


class FetchFailure(BaseModel):
    """采集失败记录：标记无法判定并给出失败地址。"""

    url: str
    stage: Literal["list", "detail"]
    reason: str


class CheckResponse(BaseModel):
    status: CheckStatus
    timezone_note: str = "公告时间均按北京时间(UTC+8)解释"
    fetched_announcements: int
    blocked: list[BlockedResult] = Field(default_factory=list)
    clear: list[ClearResult] = Field(default_factory=list)
    failures: list[FetchFailure] = Field(default_factory=list)
    message: Optional[str] = None


# ---- 公告变更检测 ---------------------------------------------------------

# 变更检测状态：
# - baseline_created  首次完整采集，仅建立基线
# - unchanged         完整采集且与基线一致
# - changed           完整采集且存在新增/撤下/修改，基线已替换
# - undetermined      采集不完整，未比较、未替换，旧基线保留
ChangeStatus = Literal[
    "baseline_created", "unchanged", "changed", "undetermined"
]


class ChangeRequest(BaseModel):
    """公告变更检测请求：只需公告列表入口 URL。"""

    announcement_list_url: str = Field(..., description="公告列表页 URL，按入口分别维护基线")


class AnnouncementSnapshot(BaseModel):
    """单条公告参与比较的结构化快照（以详情 URL 标识）。"""

    detail_url: str = Field(..., description="公告详情页 URL，公告的唯一标识")
    road_id: str
    direction: str
    effective_start: str = Field(..., description="生效开始日 ISO 日期")
    effective_end: Optional[str] = Field(None, description="生效结束日；null 表示长期有效")
    weekdays: list[int] = Field(..., description="每周封闭日，周一=0 ... 周日=6")
    clock_start: str = Field(..., description="开始钟点 HH:MM")
    clock_end: str = Field(..., description="结束钟点 HH:MM")
    cross_midnight: bool = Field(..., description="是否跨午夜封闭（归属开始当天）")


class ChangedAnnouncement(BaseModel):
    """一条被修改的公告：变更字段及新旧两份快照与来源 URL。"""

    detail_url: str
    changed_fields: dict[str, dict] = Field(
        ...,
        description="字段名 -> {old, new}；字段覆盖路段、方向、生效日期、封闭日、起止钟点",
    )
    old: AnnouncementSnapshot
    new: AnnouncementSnapshot


class ChangeResponse(BaseModel):
    status: ChangeStatus
    announcement_list_url: str
    complete: bool = Field(..., description="本次采集是否完整（全部分页与详情成功且无分页异常）")
    pagination_loop: bool = Field(False, description="是否遇到分页环路")
    page_limit_hit: bool = Field(False, description="是否达到分页页数上限")
    pages_fetched: int = 0
    fetched_announcements: int = Field(0, description="本次成功解析的公告条数")
    added: list[AnnouncementSnapshot] = Field(default_factory=list, description="基线中没有的新公告（新来源为本次采集）")
    removed: list[AnnouncementSnapshot] = Field(default_factory=list, description="本次列表中消失的公告（旧来源为上轮基线）")
    changed: list[ChangedAnnouncement] = Field(default_factory=list, description="同 URL 但比较字段发生变化的公告")
    failures: list[FetchFailure] = Field(default_factory=list, description="采集问题地址与原因；undetermined 时据此核对")
    baseline_created_at: Optional[str] = None
    baseline_updated_at: Optional[str] = None
    message: Optional[str] = None
