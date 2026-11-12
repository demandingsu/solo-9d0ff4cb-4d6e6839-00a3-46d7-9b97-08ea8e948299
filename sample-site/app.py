"""离线样本站：模拟公路施工封闭公告的同站分页列表与详情页。

数据全部内置，无外部依赖，供采集 API 离线联调。
页面结构与真实公告站点同构：
- /announcements?page=N  列表页（含“下一页”同站分页）
- /announcement/<id>     详情页（路段/方向/生效日期/每周封闭日/起止钟点）
- 故意提供一个失效链接，用于验证“采集失败 -> 无法判定”的处理。
"""
from __future__ import annotations

from datetime import date
from flask import Flask, abort, render_template_string, request

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
  {% if page > 1 %}<a rel="prev" href="/announcements?page={{ page - 1 }}">上一页</a>{% endif %}
  {% if has_next %}<a rel="next" href="/announcements?page={{ page + 1 }}">下一页</a>{% endif %}
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

ANNOUNCEMENTS = [
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
# 故意放在列表里的失效链接，验证采集失败处理
BROKEN_HREF = "/announcement/gg-2026-deleted"


def _all_items() -> list[dict]:
    items = [
        {"href": f"/announcement/{a['id']}", "title": a["title"], "published": a["published"]}
        for a in ANNOUNCEMENTS
    ]
    items.insert(1, {"href": BROKEN_HREF, "title": "【已撤销】临时管制公告", "published": "2026-11-04"})
    return items


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/announcements")
def listing():
    page = max(1, request.args.get("page", default=1, type=int))
    items = _all_items()
    start = (page - 1) * PAGE_SIZE
    page_items = items[start : start + PAGE_SIZE]
    if not page_items and page != 1:
        abort(404)
    has_next = start + PAGE_SIZE < len(items)
    return render_template_string(
        LIST_TEMPLATE, items=page_items, page=page, has_next=has_next
    )


@app.get("/announcement/<ann_id>")
def detail(ann_id: str):
    ann = next((a for a in ANNOUNCEMENTS if a["id"] == ann_id), None)
    if ann is None:
        abort(404)
    return render_template_string(DETAIL_TEMPLATE, ann=ann)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
