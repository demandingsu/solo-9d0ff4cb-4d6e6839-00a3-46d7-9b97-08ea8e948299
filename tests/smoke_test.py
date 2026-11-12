"""端到端冒烟测试（仅标准库）：覆盖受阻、畅通、跨午夜、生效期、无法判定等场景。"""
import json
import urllib.request

API = "http://127.0.0.1:8000/check"
LIST_URL = "http://127.0.0.1:8080/announcements?page=1"


def call(segments, list_url=LIST_URL):
    body = json.dumps(
        {"announcement_list_url": list_url, "segments": segments}
    ).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def show(name, data):
    print(f"== {name} ==")
    print("status:", data["status"], "| fetched:", data["fetched_announcements"])
    for b in data["blocked"]:
        print("  blocked:", b["road_id"], b["direction"],
              [(x["start"][5:16], x["end"][5:16]) for x in b["blocking"]],
              "links:", len(b["announcements"]))
    for c in data["clear"]:
        print("  clear  :", c["road_id"], c["direction"])
    for f in data["failures"]:
        print("  FAIL   :", f["stage"], f["url"], f["reason"])
    print()
    return data


# 1. 周一深夜驶入，撞上 G15-K123 下行 22:00-次日05:00（跨午夜，归属周一）
d1 = show("跨午夜受阻（周一 21:30 -> 周二 02:00）", call([
    {"road_id": "G15-K123", "direction": "下行",
     "enter_at": "2026-11-16T21:30:00+08:00", "exit_at": "2026-11-17T02:00:00+08:00"},
]))
assert d1["status"] == "undetermined", "存在失效链接时必须为 undetermined"
assert any(b["road_id"] == "G15-K123" for b in d1["blocked"])
hit = next(b for b in d1["blocked"] if b["road_id"] == "G15-K123")
assert hit["blocking"][0]["start"] == "2026-11-16T22:00:00+08:00"
assert hit["blocking"][0]["end"] == "2026-11-17T05:00:00+08:00"
assert hit["announcements"][0].endswith("/announcement/gg-2026-001")
assert d1["clear"] == [], "有采集失败时不得给出 clear"

# 2. 周二凌晨 02:00-04:00：仍处于周一晚开始的跨午夜封闭内
d2 = show("跨午夜尾部相交（周二 02:00 -> 04:00）", call([
    {"road_id": "G15-K123", "direction": "下行",
     "enter_at": "2026-11-17T02:00:00+08:00", "exit_at": "2026-11-17T04:00:00+08:00"},
]))
assert any(b["road_id"] == "G15-K123" for b in d2["blocked"])

# 3. 周二上午驶入上行：撞 G15-K123 上行 09:00-16:30（每周二/四）
d3 = show("白天时段受阻（周二 10:00 -> 12:00 上行）", call([
    {"road_id": "G15-K123", "direction": "上行",
     "enter_at": "2026-11-17T10:00:00+08:00", "exit_at": "2026-11-17T12:00:00+08:00"},
]))
assert any(b["road_id"] == "G15-K123" for b in d3["blocked"])

# 4. 方向不匹配：下行车辆不受上行公告影响（但下行有周一晚封闭；选周三白天验证）
d4 = show("方向不匹配且时段不相交 -> 无法判定（有失败链接）", call([
    {"road_id": "G15-K123", "direction": "下行",
     "enter_at": "2026-11-18T10:00:00+08:00", "exit_at": "2026-11-18T12:00:00+08:00"},
]))
assert d4["blocked"] == [] and d4["clear"] == [] and d4["status"] == "undetermined"

# 5. 生效日期限定：G15-K123 上行公告 11-05 才生效，11-03（周二）不应命中
d5 = show("生效期外不命中（2026-11-03 上行）", call([
    {"road_id": "G15-K123", "direction": "上行",
     "enter_at": "2026-11-03T10:00:00+08:00", "exit_at": "2026-11-03T12:00:00+08:00"},
]))
assert d5["blocked"] == []

# 6. 周末全天封闭：G25-K88 双向，周六任意时间命中
d6 = show("周末全天封闭（G25-K88 周六）", call([
    {"road_id": "G25-K88", "direction": "上行",
     "enter_at": "2026-11-14T06:00:00+08:00", "exit_at": "2026-11-14T09:00:00+08:00"},
]))
assert any(b["road_id"] == "G25-K88" for b in d6["blocked"])

# 7. 工作日不命中周末封闭（周五）
d7 = show("工作日不命中周末封闭（G25-K88 周五）", call([
    {"road_id": "G25-K88", "direction": "下行",
     "enter_at": "2026-11-13T06:00:00+08:00", "exit_at": "2026-11-13T09:00:00+08:00"},
]))
assert d7["blocked"] == []

# 8. 列表页整体不可达：list 阶段失败，全部无法判定
d8 = show("列表页不可达", call([
    {"road_id": "G15-K123", "direction": "下行",
     "enter_at": "2026-11-16T21:30:00+08:00", "exit_at": "2026-11-17T02:00:00+08:00"},
], list_url="http://127.0.0.1:8080/announcements?page=999"))
assert d8["status"] == "undetermined"
assert any(f["stage"] == "list" for f in d8["failures"])
assert d8["clear"] == []

# 9. 时区换算：以 UTC 给出的等价时间（16日13:30Z = 北京21:30）应得到相同结论
d9 = show("UTC 输入自动换算北京时间", call([
    {"road_id": "G15-K123", "direction": "下行",
     "enter_at": "2026-11-16T13:30:00Z", "exit_at": "2026-11-16T18:00:00Z"},
]))
assert any(b["road_id"] == "G15-K123" for b in d9["blocked"])
hit9 = next(b for b in d9["blocked"] if b["road_id"] == "G15-K123")
assert hit9["blocking"][0]["start"] == "2026-11-16T22:00:00+08:00"

print("全部断言通过 ✔")
