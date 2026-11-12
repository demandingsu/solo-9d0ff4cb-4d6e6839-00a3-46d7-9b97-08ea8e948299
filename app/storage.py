"""公告基线的 SQLite 持久化（按公告列表入口分别维护）。

设计要点：
- 以公告**列表入口 URL** 划分基线，以公告**详情 URL** 标识同一条公告；
- 每个列表入口对应 baseline_meta 一行 + baseline_entries 若干行；
- 新基线替换在单事务内完成（DELETE + INSERT + UPSERT 元数据），
  任何异常都不会留下半套基线，旧基线保持可用；
- 首次完整采集时该入口没有 meta，仅建立基线、不产出差异。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from .parser import ClosureAnnouncement

# 默认落在工作目录下的 data/；容器内由 compose 挂载命名卷持久化
DEFAULT_DB_PATH = os.environ.get("CLOSURE_BASELINE_DB", "data/baseline.db")

_write_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS baseline_meta (
    list_url   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baseline_entries (
    list_url        TEXT NOT NULL,
    detail_url      TEXT NOT NULL,
    road_id         TEXT NOT NULL,
    direction       TEXT NOT NULL,
    effective_start TEXT NOT NULL,
    effective_end   TEXT,
    weekdays        TEXT NOT NULL,
    clock_start     TEXT NOT NULL,
    clock_end       TEXT NOT NULL,
    cross_midnight  INTEGER NOT NULL,
    PRIMARY KEY (list_url, detail_url)
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    # 连接均在 asyncio.to_thread 的工作线程内短开短用，配合 _write_lock
    # 串行化写事务；check_same_thread=False 允许连接跟随工作线程使用
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """创建表结构（幂等）。"""
    path = db_path or DEFAULT_DB_PATH
    with _write_lock, _connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def snapshot(ann: ClosureAnnouncement) -> dict:
    """把解析出的公告转成可比较、可入库的快照字典。"""
    return {
        "detail_url": ann.url,
        "road_id": ann.road_id,
        "direction": ann.direction,
        "effective_start": ann.effective_start.isoformat(),
        "effective_end": ann.effective_end.isoformat() if ann.effective_end else None,
        "weekdays": list(ann.weekdays),
        "clock_start": ann.clock.start.isoformat(),
        "clock_end": ann.clock.end.isoformat(),
        "cross_midnight": ann.clock.cross_midnight,
    }


# 参与“是否修改”比较的字段（title 等展示性字段不参与）
COMPARED_FIELDS = (
    "road_id",
    "direction",
    "effective_start",
    "effective_end",
    "weekdays",
    "clock_start",
    "clock_end",
    "cross_midnight",
)


def load_baseline(list_url: str, db_path: Optional[str] = None) -> Optional[list[dict]]:
    """读取某列表入口的基线条目；入口从未建立基线时返回 None。"""
    path = db_path or DEFAULT_DB_PATH
    init_db(path)
    with _connect(path) as conn:
        meta = conn.execute(
            "SELECT 1 FROM baseline_meta WHERE list_url = ?", (list_url,)
        ).fetchone()
        if meta is None:
            return None
        rows = conn.execute(
            "SELECT * FROM baseline_entries WHERE list_url = ? ORDER BY detail_url",
            (list_url,),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]


def get_meta(list_url: str, db_path: Optional[str] = None) -> Optional[dict]:
    path = db_path or DEFAULT_DB_PATH
    init_db(path)
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT list_url, created_at, updated_at FROM baseline_meta WHERE list_url = ?",
            (list_url,),
        ).fetchone()
        return dict(row) if row else None


def replace_baseline(
    list_url: str, entries: list[dict], db_path: Optional[str] = None
) -> dict:
    """原子替换某列表入口的基线，返回更新后的 meta。

    仅在完整采集成功后调用。事务内先删后插，失败则整体回滚，
    旧基线原样保留。
    """
    path = db_path or DEFAULT_DB_PATH
    init_db(path)
    now = datetime.now(timezone.utc).isoformat()
    with _write_lock, _connect(path) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existed = conn.execute(
                "SELECT created_at FROM baseline_meta WHERE list_url = ?", (list_url,)
            ).fetchone()
            created_at = existed["created_at"] if existed else now
            conn.execute(
                "INSERT INTO baseline_meta(list_url, created_at, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(list_url) DO UPDATE SET updated_at = excluded.updated_at",
                (list_url, created_at, now),
            )
            conn.execute(
                "DELETE FROM baseline_entries WHERE list_url = ?", (list_url,)
            )
            conn.executemany(
                "INSERT INTO baseline_entries("
                "list_url, detail_url, road_id, direction, effective_start, "
                "effective_end, weekdays, clock_start, clock_end, cross_midnight) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        list_url,
                        e["detail_url"],
                        e["road_id"],
                        e["direction"],
                        e["effective_start"],
                        e["effective_end"],
                        json.dumps(e["weekdays"], ensure_ascii=False),
                        e["clock_start"],
                        e["clock_end"],
                        1 if e["cross_midnight"] else 0,
                    )
                    for e in entries
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    meta = get_meta(list_url, path)
    assert meta is not None
    return meta


def _row_to_entry(row: sqlite3.Row) -> dict:
    return {
        "detail_url": row["detail_url"],
        "road_id": row["road_id"],
        "direction": row["direction"],
        "effective_start": row["effective_start"],
        "effective_end": row["effective_end"],
        "weekdays": json.loads(row["weekdays"]),
        "clock_start": row["clock_start"],
        "clock_end": row["clock_end"],
        "cross_midnight": bool(row["cross_midnight"]),
    }
