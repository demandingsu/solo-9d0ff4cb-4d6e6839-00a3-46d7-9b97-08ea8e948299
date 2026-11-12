"""公告变更 API 端到端冒烟测试（仅标准库）。

依赖本地运行的：
- API       http://127.0.0.1:8000
- 样本站    http://127.0.0.1:8080（含 /admin 场景控制）

覆盖：首次建基线、无变更、修改（新旧来源）、新增、撤下、
详情故障页、列表故障页、分页环路下的无法判定与旧基线保留、
漏采不计撤下、按列表入口分别维护基线。
"""
import json
import urllib.request

API = "http://127.0.0.1:8000/changes"
SITE = "http://127.0.0.1:8080"
PORTAL = f"{SITE}/portal?page=1"
LEGACY = f"{SITE}/announcements?page=1"


def _req(url, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def changes(list_url):
    _, data = _req(API, {"announcement_list_url": list_url}, method="POST")
    return data


def admin(path, method="POST", payload=None):
    status, data = _req(f"{SITE}/admin{path}", payload, method)
    assert status == 200, (path, status, data)
    return data


def show(name, data):
    print(f"== {name} ==")
    print("  status:", data["status"], "| complete:", data["complete"],
          "| fetched:", data["fetched_announcements"])
    for a in data["added"]:
        print("  + 新增:", a["detail_url"], a["road_id"], a["direction"])
    for a in data["removed"]:
        print("  - 撤下:", a["detail_url"], a["road_id"], a["direction"])
    for c in data["changed"]:
        print("  ~ 修改:", c["detail_url"], "字段:", sorted(c["changed_fields"]))
    for f in data["failures"]:
        print("  ! 问题:", f["stage"], f["url"], f["reason"])
    print("   ", data["message"])
    print()
    return data


# 0. 健康场景重置：两个入口均无失效链接
admin("/reset?scenario=healthy")

# 1. 首次完整采集：只建立基线，不产出变更
d1 = show("首次完整采集 -> baseline_created", changes(PORTAL))
assert d1["status"] == "baseline_created"
assert d1["complete"] is True and d1["fetched_announcements"] == 3
assert d1["added"] == d1["removed"] == d1["changed"] == []
assert d1["baseline_created_at"] and d1["baseline_updated_at"]

# 2. 再次采集且站点无变化 -> unchanged
d2 = show("无变化 -> unchanged", changes(PORTAL))
assert d2["status"] == "unchanged"
assert d2["added"] == d2["removed"] == d2["changed"] == []
assert d2["baseline_created_at"] == d1["baseline_created_at"]

# 3. 修改公告 001 的封闭日与起止钟点 -> changed，含新旧两份快照与来源 URL
admin("/announcements/gg-2026-001/update", "POST", {
    "weekday_text": "每周一、周三、周五",
    "clock_text": "21:00 至 次日 06:00",
})
d3 = show("修改封闭日与钟点 -> changed", changes(PORTAL))
assert d3["status"] == "changed"
assert len(d3["changed"]) == 1 and d3["added"] == [] and d3["removed"] == []
chg = d3["changed"][0]
assert chg["detail_url"].endswith("/announcement/gg-2026-001")
assert set(chg["changed_fields"]) == {"weekdays", "clock_start", "clock_end"}
assert chg["changed_fields"]["weekdays"] == {"old": [0, 2], "new": [0, 2, 4]}
assert chg["old"]["clock_start"] == "22:00:00" and chg["new"]["clock_start"] == "21:00:00"
assert chg["old"]["detail_url"] == chg["new"]["detail_url"]

# 4. 新增公告 004 -> added（新来源即本次采集的详情 URL）
admin("/announcements", "POST", {
    "id": "gg-2026-004",
    "title": "G45大广高速K210段下行施工封闭公告",
    "published": "2026-11-10",
    "road_id": "G45-K210",
    "direction": "下行",
    "effective_text": "2026年11月12日至2026年12月12日",
    "weekday_text": "每周五",
    "clock_text": "08:00 至 12:00",
})
d4 = show("新增公告 -> added", changes(PORTAL))
assert d4["status"] == "changed" and len(d4["added"]) == 1
assert d4["added"][0]["detail_url"].endswith("/announcement/gg-2026-004")
assert d4["added"][0]["road_id"] == "G45-K210"
assert d4["removed"] == [] and d4["changed"] == []

# 5. 撤下公告 002 -> removed（旧来源为上轮基线中的详情 URL）
admin("/announcements/gg-2026-002", "DELETE")
d5 = show("撤下公告 -> removed", changes(PORTAL))
assert d5["status"] == "changed" and len(d5["removed"]) == 1
assert d5["removed"][0]["detail_url"].endswith("/announcement/gg-2026-002")
assert d5["removed"][0]["road_id"] == "G15-K123"
assert d5["added"] == [] and d5["changed"] == []
# 撤下后详情页确实 404
code, _ = _req(f"{SITE}/announcement/gg-2026-002")
assert code == 404

# 6. 详情故障页：采集不完整 -> undetermined，不比较、不替换基线
admin("/faults/detail/gg-2026-003")
d6 = show("详情故障 -> undetermined", changes(PORTAL))
assert d6["status"] == "undetermined" and d6["complete"] is False
assert any(f["stage"] == "detail" and f["url"].endswith("gg-2026-003") for f in d6["failures"])
assert d6["added"] == d6["removed"] == d6["changed"] == []

# 7. 故障期间站点再撤下 001；因采集不完整，绝不能把漏采/撤下混报
admin("/announcements/gg-2026-001", "DELETE")
d7 = show("故障期间再撤下 -> 仍 undetermined", changes(PORTAL))
assert d7["status"] == "undetermined"
assert d7["removed"] == []

# 8. 清除详情故障后完整采集：基线保留在第 5 步状态，应一次性报出 001 撤下，
#    且 003 从未被当成撤下（漏采不计撤下的核心验证）
admin("/faults/detail/gg-2026-003", "DELETE")
d8 = show("故障恢复 -> 仅报真实撤下", changes(PORTAL))
assert d8["status"] == "changed"
removed_ids = [r["detail_url"].rsplit("/", 1)[-1] for r in d8["removed"]]
assert removed_ids == ["gg-2026-001"], removed_ids
assert all("gg-2026-003" not in r["detail_url"] for r in d8["removed"])

# 9. 列表故障页（第 2 页 500）-> undetermined，问题地址带 list 阶段
#    先补一条公告使列表达到 2 页（PAGE_SIZE=2）
admin("/announcements", "POST", {
    "id": "gg-2026-005",
    "title": "G50沪渝高速K55段上行施工封闭公告",
    "published": "2026-11-11",
    "road_id": "G50-K55",
    "direction": "上行",
    "effective_text": "2026年11月12日至2026年11月20日",
    "weekday_text": "每周四",
    "clock_text": "10:00 至 15:00",
})
d9a = changes(PORTAL)  # 先把 005 纳入基线，避免干扰故障判定
assert d9a["status"] == "changed" and len(d9a["added"]) == 1
admin("/faults/list/2")
d9 = show("列表故障页 -> undetermined", changes(PORTAL))
assert d9["status"] == "undetermined"
assert any(f["stage"] == "list" and "page=2" in f["url"] for f in d9["failures"])
admin("/faults/list/2", "DELETE")

# 10. 分页环路：第 2 页下一页指回第 1 页 -> undetermined 且 pagination_loop=True
admin("/faults/loop/2")
d10 = show("分页环路 -> undetermined", changes(PORTAL))
assert d10["status"] == "undetermined"
assert d10["pagination_loop"] is True and d10["page_limit_hit"] is False
assert any("分页环路" in f["reason"] for f in d10["failures"])
admin("/faults/loop/2", "DELETE")

# 11. 按列表入口分别维护基线：默认场景的 /announcements 含失效链接，
#     首次采集即不完整 -> 不建立基线
admin("/reset?scenario=default")
d11 = show("另一入口首次采集不完整 -> undetermined 且不建基线", changes(LEGACY))
assert d11["status"] == "undetermined"
assert any(f["stage"] == "detail" and f["url"].endswith("gg-2026-deleted")
           for f in d11["failures"])
# 切换健康场景后该入口仍应是“首次建基线”，证明上一次不完整采集未写基线
admin("/reset?scenario=healthy")
d12 = show("同一入口完整采集 -> baseline_created", changes(LEGACY))
assert d12["status"] == "baseline_created"

# 12. 两个入口基线互不影响：portal 的基线仍在（当前站点 3 条：003/004 + ?）
d13 = show("portal 入口基线独立保留", changes(PORTAL))
assert d13["status"] in ("unchanged", "changed")
assert d13["complete"] is True

print("全部断言通过 ✔")
