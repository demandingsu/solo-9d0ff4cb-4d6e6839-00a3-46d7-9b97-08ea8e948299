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
