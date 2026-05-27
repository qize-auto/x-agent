"""
ErrorLedger — 系统错误台账
===========================
统一记录、去重、持久化系统级错误，供 GUI/CLI 展示和后续分析。

设计原则：
- 与 Telemetry 解耦：无论 telemetry 是否启用，系统级错误都记录
- 与 Self-Improve 解耦：无论 self_improve 是否启用，错误都记录
- 指纹去重：相同错误不重复记录
- 已读标记：用户确认后不再重复提醒
- JSONL 格式：每行一个 JSON，便于 jq/pandas 分析
"""
from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ErrorRecord:
    """单条错误记录"""
    timestamp: float
    category: str           # config_validation / import_failure / runtime / adaptive
    fingerprint: str        # 16 位哈希，用于去重
    message: str            # 错误摘要（用户可读）
    detail: str = ""        # 详细错误信息
    context: dict = field(default_factory=dict)
    acknowledged: bool = False   # 用户是否已确认（GUI 已展示过）


class ErrorLedger:
    """
    错误台账。

    用法:
        ledger = ErrorLedger()
        fp = ledger.record("config_validation", "阈值超出范围", detail="cpu_threshold=150")
        unacked = ledger.get_unacknowledged()   # 获取未确认的
        ledger.acknowledge(fp)                  # 标记已确认
    """

    def __init__(self, ledger_path: str | Path | None = None):
        if ledger_path is None:
            ledger_path = Path.home() / ".xagent" / "error_ledger.jsonl"
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def _compute_fingerprint(self, category: str, message: str) -> str:
        """基于类别+消息生成稳定指纹"""
        return hashlib.sha256(f"{category}:{message}".encode("utf-8")).hexdigest()[:16]

    def record(self, category: str, message: str, detail: str = "",
               context: dict | None = None, force: bool = False) -> str:
        """
        记录一条错误。若相同指纹已存在且未 force，则不重复记录。

        Returns:
            错误指纹
        """
        fingerprint = self._compute_fingerprint(category, message)

        if not force:
            # 检查最近 7 天内是否已有相同指纹
            existing = self._find_by_fingerprint(fingerprint, max_age_sec=604800)
            if existing:
                return fingerprint

        record = ErrorRecord(
            timestamp=time.time(),
            category=category,
            fingerprint=fingerprint,
            message=message,
            detail=detail,
            context=context or {},
            acknowledged=False,
        )

        try:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

        return fingerprint

    def _find_by_fingerprint(self, fingerprint: str, max_age_sec: float = 604800) -> Optional[ErrorRecord]:
        """查找最近 N 秒内是否有相同指纹的记录"""
        cutoff = time.time() - max_age_sec
        if not self.ledger_path.exists():
            return None
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("fingerprint") == fingerprint and data.get("timestamp", 0) > cutoff:
                            return ErrorRecord(**data)
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    def get_unacknowledged(self, categories: list[str] | None = None,
                           max_age_sec: float = 604800) -> list[ErrorRecord]:
        """获取未确认的错误列表（默认最近 7 天）"""
        cutoff = time.time() - max_age_sec
        results = []
        if not self.ledger_path.exists():
            return results

        seen_fp = set()
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("timestamp", 0) < cutoff:
                            continue
                        if data.get("acknowledged", False):
                            continue
                        fp = data.get("fingerprint", "")
                        if fp in seen_fp:
                            continue
                        seen_fp.add(fp)
                        if categories is None or data.get("category") in categories:
                            results.append(ErrorRecord(**data))
                    except Exception:
                        continue
        except Exception:
            pass
        return results

    def acknowledge(self, fingerprint: str):
        """标记某条错误为已确认（全量扫描重写文件）"""
        if not self.ledger_path.exists():
            return
        lines = []
        changed = False
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("fingerprint") == fingerprint and not data.get("acknowledged", False):
                            data["acknowledged"] = True
                            changed = True
                        lines.append(json.dumps(data, ensure_ascii=False, default=str))
                    except Exception:
                        lines.append(line)
            if changed:
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def acknowledge_all(self, categories: list[str] | None = None):
        """批量确认某类错误"""
        if not self.ledger_path.exists():
            return
        lines = []
        changed = False
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not data.get("acknowledged", False):
                            if categories is None or data.get("category") in categories:
                                data["acknowledged"] = True
                                changed = True
                        lines.append(json.dumps(data, ensure_ascii=False, default=str))
                    except Exception:
                        lines.append(line)
            if changed:
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def stats(self) -> dict:
        """返回错误统计"""
        total = 0
        unacked = 0
        by_category = {}
        if self.ledger_path.exists():
            try:
                with open(self.ledger_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            total += 1
                            cat = data.get("category", "unknown")
                            by_category[cat] = by_category.get(cat, 0) + 1
                            if not data.get("acknowledged", False):
                                unacked += 1
                        except Exception:
                            continue
            except Exception:
                pass
        return {"total": total, "unacknowledged": unacked, "by_category": by_category}
