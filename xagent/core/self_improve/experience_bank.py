"""
ExperienceBank
==============
SQLite 持久化存储失败经验。
支持频率统计、查询、TTL 清理。
"""
from __future__ import annotations
import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ExperienceRecord:
    """单条失败经验记录"""
    id: int = 0
    failure_type: str = ""
    root_cause: str = ""
    evidence: str = ""
    trace_id: str = ""
    original_prompt: str = ""
    suggested_fix: str = ""
    frequency: int = 1
    last_seen: float = 0.0
    hit_count: int = 0  # 被成功用于修复的次数


class ExperienceBank:
    """
    经验银行。

    存储结构（SQLite）:
        experiences(
            id INTEGER PRIMARY KEY,
            failure_type TEXT,
            root_cause TEXT,
            evidence TEXT,
            trace_id TEXT,
            original_prompt TEXT,
            suggested_fix TEXT,
            frequency INTEGER DEFAULT 1,
            last_seen REAL,
            hit_count INTEGER DEFAULT 0
        )
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path.home() / ".xagent" / "experience_bank.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                failure_type TEXT NOT NULL,
                root_cause TEXT,
                evidence TEXT,
                trace_id TEXT,
                original_prompt TEXT,
                suggested_fix TEXT,
                frequency INTEGER DEFAULT 1,
                last_seen REAL DEFAULT 0,
                hit_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_failure_type ON experiences(failure_type)
        """)
        conn.commit()
        conn.close()

    def record(self, failure_type: str, root_cause: str = "", evidence: str = "",
               trace_id: str = "", original_prompt: str = "", suggested_fix: str = "") -> int:
        """
        记录一次失败经验。如果同类型+同根因已存在，则 frequency+1。
        Returns: 记录 ID
        """
        conn = sqlite3.connect(self.db_path)
        now = time.time()

        # 检查是否已有相似记录
        cursor = conn.execute(
            "SELECT id, frequency FROM experiences WHERE failure_type = ? AND root_cause = ?",
            (failure_type, root_cause),
        )
        row = cursor.fetchone()

        if row:
            record_id, freq = row
            conn.execute(
                "UPDATE experiences SET frequency = ?, last_seen = ? WHERE id = ?",
                (freq + 1, now, record_id),
            )
            conn.commit()
            conn.close()
            return record_id

        # 新建记录
        cursor = conn.execute(
            """INSERT INTO experiences
               (failure_type, root_cause, evidence, trace_id, original_prompt, suggested_fix, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (failure_type, root_cause, evidence, trace_id, original_prompt, suggested_fix, now),
        )
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return record_id

    def get_frequent(self, failure_type: str = None, min_frequency: int = 2, limit: int = 10) -> list[ExperienceRecord]:
        """获取高频失败经验"""
        conn = sqlite3.connect(self.db_path)
        if failure_type:
            cursor = conn.execute(
                "SELECT * FROM experiences WHERE failure_type = ? AND frequency >= ? ORDER BY frequency DESC LIMIT ?",
                (failure_type, min_frequency, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM experiences WHERE frequency >= ? ORDER BY frequency DESC LIMIT ?",
                (min_frequency, limit),
            )
        rows = cursor.fetchall()
        conn.close()

        records = []
        for row in rows:
            records.append(ExperienceRecord(
                id=row[0],
                failure_type=row[1],
                root_cause=row[2],
                evidence=row[3],
                trace_id=row[4],
                original_prompt=row[5],
                suggested_fix=row[6],
                frequency=row[7],
                last_seen=row[8],
                hit_count=row[9],
            ))
        return records

    def increment_hit(self, record_id: int):
        """某条经验成功帮助修复时增加 hit_count"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE experiences SET hit_count = hit_count + 1 WHERE id = ?",
            (record_id,),
        )
        conn.commit()
        conn.close()

    def stats(self) -> dict:
        """统计信息"""
        conn = sqlite3.connect(self.db_path)
        total = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]
        type_dist = conn.execute(
            "SELECT failure_type, COUNT(*), SUM(frequency) FROM experiences GROUP BY failure_type"
        ).fetchall()
        conn.close()
        return {
            "total_records": total,
            "type_distribution": {t: {"records": c, "total_freq": f} for t, c, f in type_dist},
        }

    def cleanup(self, max_age_sec: float = 604800):
        """清理过期记录（默认 7 天）"""
        conn = sqlite3.connect(self.db_path)
        cutoff = time.time() - max_age_sec
        conn.execute("DELETE FROM experiences WHERE last_seen < ?", (cutoff,))
        conn.commit()
        conn.close()
