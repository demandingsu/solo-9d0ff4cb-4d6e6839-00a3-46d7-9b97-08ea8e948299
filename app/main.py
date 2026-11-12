"""FastAPI 入口：冷链配送施工封闭核对 API。"""
from __future__ import annotations

from fastapi import FastAPI

from .models import CheckRequest, CheckResponse
from .scheduler import build_response
from .scraper import scrape

app = FastAPI(
    title="冷链配送施工封闭核对 API",
    description=(
        "传入公告列表页 URL 与计划路段（编号、方向、带时区驶入/驶出时间），"
        "采集同站分页与详情并返回受阻路段及公告链接；"
        "采集失败时标记无法判定并给出失败地址。"
    ),
    version="1.0.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
async def check(request: CheckRequest) -> CheckResponse:
    scrape_result = await scrape(request.announcement_list_url)
    return build_response(request.segments, scrape_result)
