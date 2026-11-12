"""FastAPI 入口：冷链配送施工封闭核对与公告变更检测 API。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .changes import detect_changes
from .models import (
    ChangeRequest,
    ChangeResponse,
    CheckRequest,
    CheckResponse,
)
from .scheduler import build_response
from .scraper import scrape
from .storage import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时建好基线表，保证首个请求即可读写
    init_db()
    yield


app = FastAPI(
    title="冷链配送施工封闭核对 API",
    description=(
        "传入公告列表页 URL 与计划路段（编号、方向、带时区驶入/驶出时间），"
        "采集同站分页与详情并返回受阻路段及公告链接；"
        "采集失败时标记无法判定并给出失败地址。"
        "另提供公告变更检测：按列表入口维护 SQLite 基线，"
        "比较新增、撤下与修改的施工封闭。"
    ),
    version="1.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
async def check(request: CheckRequest) -> CheckResponse:
    scrape_result = await scrape(request.announcement_list_url)
    return build_response(request.segments, scrape_result)


@app.post(
    "/changes",
    response_model=ChangeResponse,
    summary="检测公告列表入口相对上轮基线的变更",
)
async def changes(request: ChangeRequest) -> ChangeResponse:
    """完整采集成功才比较并原子替换基线；否则标记无法判定并保留旧基线。"""
    return await detect_changes(request.announcement_list_url)
