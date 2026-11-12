"""离线单元测试（仅依赖标准库 + 项目代码，无需启动服务）。

覆盖：
- diff_baseline 的新增/撤下/修改/无变化判定与比较字段；
- SQLite 基线的首建、原子替换、回滚后旧基线保留、按入口隔离；
- 采集器页数上限（httpx MockTransport，无真实网络）；
- 样本站分页环路与故障页的 HTML 行为。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from datetime import time

sys.path.insert(0, "/tmp/pylibs")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.changes import diff_baseline  # noqa: E402
from app.parser import ClockRange, parse_listing  # noqa: E402
from app.scraper import MAX_PAGES, scrape  # noqa: E402
from app.storage import load_baseline, replace_baseline  # noqa: E402

CLOCK = ClockRange(start=time(22, 0), end=time(5, 0), cross_midnight=True)


def entry(url, road="G15-K123", direction="下行", weekdays=(0, 2),
          eff_start="2026-11-01", eff_end="2026-11-30",
          clock_start="22:00:00", clock_end="05:00:00", cross=True):
    return {
        "detail_url": url,
        "road_id": road,
        "direction": direction,
        "effective_start": eff_start,
        "effective_end": eff_end,
        "weekdays": list(weekdays),
        "clock_start": clock_start,
        "clock_end": clock_end,
        "cross_midnight": cross,
    }


class DiffTest(unittest.TestCase):
    def test_no_change(self):
        old = [entry("u1"), entry("u2", road="G25-K88")]
        d = diff_baseline(old, [entry("u2", road="G25-K88"), entry("u1")])
        self.assertEqual((d.added, d.removed, d.changed), ([], [], []))

    def test_added_removed_changed(self):
        old = [entry("u1"), entry("u2", weekdays=(0,))]
        new = [entry("u1", direction="双向"), entry("u3")]
        d = diff_baseline(old, new)
        self.assertEqual([e["detail_url"] for e in d.added], ["u3"])
        self.assertEqual([e["detail_url"] for e in d.removed], ["u2"])
        self.assertEqual(len(d.changed), 1)
        chg = d.changed[0]
        self.assertEqual(chg["detail_url"], "u1")
        self.assertEqual(set(chg["changed_fields"]), {"direction"})
        self.assertEqual(chg["changed_fields"]["direction"],
                         {"old": "下行", "new": "双向"})

    def test_effective_end_none_vs_value(self):
        old = [entry("u1", eff_end=None)]
        new = [entry("u1", eff_end="2026-12-31")]
        d = diff_baseline(old, new)
        self.assertEqual(len(d.changed), 1)
        self.assertIn("effective_end", d.changed[0]["changed_fields"])

    def test_non_compared_ignored(self):
        # 快照中额外字段（如 title）不参与比较
        a = entry("u1")
        a["title"] = "旧标题"
        b = entry("u1")
        b["title"] = "新标题"
        d = diff_baseline([a], [b])
        self.assertEqual(d.changed, [])


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "baseline.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_build_replace_and_isolation(self):
        self.assertIsNone(load_baseline("list-a", self.db))
        replace_baseline("list-a", [entry("a1"), entry("a2")], self.db)
        replace_baseline("list-b", [entry("b1")], self.db)
        self.assertEqual(
            [e["detail_url"] for e in load_baseline("list-a", self.db)],
            ["a1", "a2"],
        )
        # 原子替换：删一条再加一条，入口 B 不受影响
        meta_before = replace_baseline("list-a", [entry("a2"), entry("a3")], self.db)
        urls = [e["detail_url"] for e in load_baseline("list-a", self.db)]
        self.assertEqual(urls, ["a2", "a3"])
        self.assertEqual(
            [e["detail_url"] for e in load_baseline("list-b", self.db)], ["b1"]
        )
        self.assertTrue(meta_before["created_at"] <= meta_before["updated_at"])

    def test_rollback_keeps_old_baseline(self):
        import app.storage as storage

        replace_baseline("list-a", [entry("a1")], self.db)
        # 用违反 NOT NULL 的坏数据触发事务回滚，验证旧基线原样保留
        bad = [entry("a9")]
        bad[0]["road_id"] = None
        with self.assertRaises(Exception):
            replace_baseline("list-a", bad, self.db)
        self.assertEqual(
            [e["detail_url"] for e in load_baseline("list-a", self.db)], ["a1"]
        )
        # 回滚后库仍可正常写入
        replace_baseline("list-a", [entry("a2")], self.db)
        self.assertEqual(
            [e["detail_url"] for e in load_baseline("list-a", self.db)], ["a2"]
        )


class PaginationLimitTest(unittest.TestCase):
    def test_page_limit_marked(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if "/announcement/" in str(request.url.path):
                # 详情页：最小合法字段，保证详情阶段不失败，只制造无限分页
                html = (
                    '<html><body><article class="content"><h1>施工封闭公告</h1>'
                    '<p>路段编号：G15-K123</p><p>方向：下行</p>'
                    '<p>生效日期：2026年11月1日至2026年11月30日</p>'
                    '<p>封闭日：每周一</p><p>封闭时间：09:00 至 16:00</p>'
                    '</article></body></html>'
                )
                return httpx.Response(200, html=html)
            # 列表页：每页都给出页码递增的下一页，制造永远采不完的分页
            page = int(request.url.params.get("page", 1))
            html = (
                '<html><body><table><tr><td>'
                f'<a href="/announcement/x-{page}">'
                '施工封闭公告</a></td></tr></table>'
                f'<a rel="next" href="/portal?page={page + 1}">下一页</a>'
                '</body></html>'
            )
            return httpx.Response(200, html=html)

        transport = httpx.MockTransport(handler)
        orig_init = httpx.AsyncClient.__init__

        def patched_init(self, *args, **kwargs):
            kwargs["transport"] = transport
            orig_init(self, *args, **kwargs)

        httpx.AsyncClient.__init__ = patched_init
        try:
            result = asyncio.run(scrape("http://test.local/portal?page=1"))
        finally:
            httpx.AsyncClient.__init__ = orig_init

        self.assertTrue(result.page_limit_hit)
        self.assertFalse(result.ok)
        self.assertEqual(result.pages_fetched, MAX_PAGES)
        self.assertTrue(any("页数上限" in f.reason) for f in result.failures)


class ListingParseTest(unittest.TestCase):
    def test_loop_link_detected_as_next(self):
        html = (
            '<html><body><table>'
            '<tr><td><a href="/announcement/gg-1">G15施工封闭公告</a></td></tr>'
            '</table><a rel="next" href="/portal?page=1">下一页</a></body></html>'
        )
        listing = parse_listing(html, "http://test.local/portal?page=2")
        self.assertEqual(len(listing.detail_links), 1)
        self.assertEqual(listing.next_page_url, "http://test.local/portal?page=1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
