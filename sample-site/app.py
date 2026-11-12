"""离线样本站：模拟公路施工封闭公告的同站分页列表与详情页。

数据全部内置，无外部依赖，供采集/变更 API 离线联调。
页面结构与真实公告站点同构：
- /announcements?page=N  列表入口 A（默认含一个失效链接，演示采集失败）
- /portal?page=N          列表入口 B（健康入口，用于公告变更基线演示）
- /announcement/<id>      详情页（路段/方向/生效日期/每周封闭日/起止钟点）

通过 /admin 下的控制接口可以在运行中模拟公告的新增、修改、撤下，
以及详情故障页、列表故障页和分页环路，无需重启容器。
"""
from __future__ import annotations

import copy
from datetime import date

from flask import Flask, abort, jsonify, render_template_string, request

app = Flask(__name__)

LIST_TEMPLATE = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>施工封闭公告列表</title></head>
<body>
<h1>公路施工封闭公告</h1>
<table class="announcement-table">
  <thead><tr><th>公告标题</th><th>发布日期</th></tr></thead>
  <tbody>
  {% for item in items %}
    <tr>
      <td><a href="{{ item.href }}">{{ item.title }}</a></td>
      <td>{{ item.published }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<div class="pagination">
  {% if page > 1 %}<a rel="prev" href="{{ entry }}?page={{ page - 1 }}">上一页</a>{% endif %}
  {% if loop_back %}<a rel="next" href="{{ entry }}?page={{ loop_back }}">下一页</a>
  {% elif has_next %}<a rel="next" href="{{ entry }}?page={{ page + 1 }}">下一页</a>{% endif %}
</div>
</body></html>
"""

DETAIL_TEMPLATE = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{{ ann.title }}</title></head>
<body>
<article class="content">
  <h1>{{ ann.title }}</h1>
  <p>路段编号：{{ ann.road_id }}</p>
  <p>方向：{{ ann.direction }}</p>
  <p>生效日期：{{ ann.effective_text }}</p>
  <p>封闭日：{{ ann.weekday_text }}</p>
  <p>封闭时间：{{ ann.clock_text }}</p>
  <p>管制措施：施工期间该路段禁止车辆通行，请提前绕行。</p>
</article>
</body></html>
"""

INITIAL_ANNOUNCEMENTS = [
    {
        "id": "gg-2026-001",
        "title": "G15沈海高速K123段下行夜间施工封闭公告",
        "published": "2026-11-01",
        "road_id": "G15-K123",
        "direction": "下行",
        "effective_start": date(2026, 11, 1),
        "effective_end": date(2026, 11, 30),
        "effective_text": "2026年11月1日至2026年11月30日",
        "weekday_text": "每周一、周三",
        "clock_text": "22:00 至 次日 05:00（跨午夜，归属当日封闭）",
    },
    {
        "id": "gg-2026-002",
        "title": "G15沈海高速K123段上行白天施工封闭公告",
        "published": "2026-11-02",
        "road_id": "G15-K123",
        "direction": "上行",
        "effective_start": date(2026, 11, 5),
        "effective_end": date(2026, 11, 25),
        "effective_text": "2026年11月5日至2026年11月25日",
        "weekday_text": "每周二、周四",
        "clock_text": "09:00 至 16:30",
    },
    {
        "id": "gg-2026-003",
        "title": "G25长深高速K88段双向周末施工封闭公告",
        "published": "2026-11-03",
        "road_id": "G25-K88",
        "direction": "双向",
        "effective_start": date(2026, 10, 15),
        "effective_end": None,
        "effective_text": "2026年10月15日起长期有效",
        "weekday_text": "每周六、周日",
        "clock_text": "00:00 至 24:00（全天封闭）",
    },
]

PAGE_SIZE = 2
# 默认场景下列表里的失效链接（已撤销公告，详情 404），验证采集失败处理
BROKEN_HREF = "/announcement/gg-2026-deleted"

# 可变运行状态（由 /admin 接口控制）
STATE: dict = {}


def _initial_state() -> dict:
    return {
        "announcements": copy.deepcopy(INITIAL_ANNOUNCEMENTS),
        "removed_ids": set(),          # 撤下：列表移除且详情 404
        "detail_faults": set(),        # 详情故障：该 id 返回 500
        "list_fault_pages": set(),     # 列表故障：该页码返回 500
        "loop_pages": set(),           # 分页环路：该页“下一页”指回上一已采集页
        "include_broken_link": True,   # /announcements 是否含失效链接
    }


def reset_state(scenario: str = "default") -> None:
    global STATE
    STATE = _initial_state()
    if scenario == "healthy":
        # 健康场景：两个入口都没有失效链接，适合演示基线变更
        STATE["include_broken_link"] = False


reset_state("default")


def _all_items(include_broken: bool) -> list[dict]:
    items = [
        {
            "href": f"/announcement/{a['id']}",
            "title": a["title"],
            "published": a["published"],
        }
        for a in STATE["announcements"]
        if a["id"] not in STATE["removed_ids"]
    ]
    if include_broken and STATE["include_broken_link"]:
        items.insert(1, {
            "href": BROKEN_HREF,
            "title": "【已撤销】临时管制公告",
            "published": "2026-11-04",
        })
    return items


def _find_announcement(ann_id: str):
    return next((a for a in STATE["announcements"] if a["id"] == ann_id), None)


def _render_listing(entry: str):
    page = max(1, request.args.get("page", default=1, type=int))
    if page in STATE["list_fault_pages"]:
        abort(500, description=f"模拟列表故障页：第 {page} 页")

    # /portal 永远是健康入口；/announcements 保留默认失效链接场景
    items = _all_items(include_broken=(entry == "/announcements"))
    start = (page - 1) * PAGE_SIZE
    page_items = items[start: start + PAGE_SIZE]
    if not page_items and page != 1:
        abort(404)
    has_next = start + PAGE_SIZE < len(items)
    # 环路：当前页的下一页指回之前已采集页（第 1 页），采集器据此识别环路
    loop_back = 1 if page in STATE["loop_pages"] and page > 1 else None
    return render_template_string(
        LIST_TEMPLATE,
        items=page_items, page=page, has_next=has_next,
        entry=entry, loop_back=loop_back,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/announcements")
def listing_legacy():
    return _render_listing("/announcements")


@app.get("/portal")
def listing_portal():
    return _render_listing("/portal")


@app.get("/announcement/<ann_id>")
def detail(ann_id: str):
    if ann_id in STATE["detail_faults"]:
        abort(500, description=f"模拟详情故障页：{ann_id}")
    if ann_id in STATE["removed_ids"]:
        abort(404)
    ann = _find_announcement(ann_id)
    if ann is None:
        abort(404)
    return render_template_string(DETAIL_TEMPLATE, ann=ann)


# ---- 场景控制接口（测试/演示用） ------------------------------------------

_EDITABLE_FIELDS = (
    "title", "road_id", "direction", "effective_text",
    "weekday_text", "clock_text", "published",
)


@app.post("/admin/reset")
def admin_reset():
    scenario = request.args.get("scenario", "default")
    if scenario not in ("default", "healthy"):
        abort(400, description="scenario 仅支持 default / healthy")
    reset_state(scenario)
    return jsonify({"ok": True, "scenario": scenario, "state": _state_view()})


@app.get("/admin/state")
def admin_state():
    return jsonify(_state_view())


@app.post("/admin/announcements")
def admin_add():
    payload = request.get_json(force=True)
    if not payload.get("id"):
        abort(400, description="必须提供 id")
    if _find_announcement(payload["id"]) is not None:
        abort(409, description=f"公告 {payload['id']} 已存在")
    ann = copy.deepcopy(INITIAL_ANNOUNCEMENTS[0])
    ann.update({k: v for k, v in payload.items() if k in _EDITABLE_FIELDS or k == "id"})
    STATE["announcements"].append(ann)
    return jsonify({"ok": True, "state": _state_view()})


@app.post("/admin/announcements/<ann_id>/update")
def admin_update(ann_id: str):
    ann = _find_announcement(ann_id)
    if ann is None or ann_id in STATE["removed_ids"]:
        abort(404)
    payload = request.get_json(force=True)
    for key, value in payload.items():
        if key in _EDITABLE_FIELDS:
            ann[key] = value
    return jsonify({"ok": True, "announcement": ann})


@app.delete("/admin/announcements/<ann_id>")
def admin_remove(ann_id: str):
    ann = _find_announcement(ann_id)
    if ann is None:
        abort(404)
    STATE["removed_ids"].add(ann_id)
    return jsonify({"ok": True, "state": _state_view()})


@app.post("/admin/faults/detail/<ann_id>")
def admin_detail_fault(ann_id: str):
    if _find_announcement(ann_id) is None:
        abort(404)
    STATE["detail_faults"].add(ann_id)
    return jsonify({"ok": True, "state": _state_view()})


@app.delete("/admin/faults/detail/<ann_id>")
def admin_detail_fault_clear(ann_id: str):
    STATE["detail_faults"].discard(ann_id)
    return jsonify({"ok": True, "state": _state_view()})


@app.post("/admin/faults/list/<int:page>")
def admin_list_fault(page: int):
    STATE["list_fault_pages"].add(page)
    return jsonify({"ok": True, "state": _state_view()})


@app.delete("/admin/faults/list/<int:page>")
def admin_list_fault_clear(page: int):
    STATE["list_fault_pages"].discard(page)
    return jsonify({"ok": True, "state": _state_view()})


@app.post("/admin/faults/loop/<int:page>")
def admin_loop(page: int):
    STATE["loop_pages"].add(page)
    return jsonify({"ok": True, "state": _state_view()})


@app.delete("/admin/faults/loop/<int:page>")
def admin_loop_clear(page: int):
    STATE["loop_pages"].discard(page)
    return jsonify({"ok": True, "state": _state_view()})


def _state_view() -> dict:
    return {
        "announcement_ids": [a["id"] for a in STATE["announcements"]],
        "removed_ids": sorted(STATE["removed_ids"]),
        "detail_faults": sorted(STATE["detail_faults"]),
        "list_fault_pages": sorted(STATE["list_fault_pages"]),
        "loop_pages": sorted(STATE["loop_pages"]),
        "include_broken_link": STATE["include_broken_link"],
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
