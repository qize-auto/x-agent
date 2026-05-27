"""
审计日志
========
记录所有工具调用，用于安全审计和事后追溯。

日志格式: JSON Lines (jsonl)
每条记录包含: timestamp, tool_name, arguments, result_summary, confirmed, duration_ms
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from datetime import datetime

AUDIT_DIR = Path.home() / ".xagent" / "audit"
AUDIT_FILE = AUDIT_DIR / f"audit_{datetime.now().strftime('%Y%m')}.jsonl"


class AuditLog:
    """审计日志记录器"""

    def __init__(self):
        self._enabled = True

    def _ensure_dir(self):
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    def log(self, tool_name: str, arguments: dict, result: dict, confirmed: bool = True, duration_ms: float = 0):
        """记录一次工具调用"""
        if not self._enabled:
            return
        try:
            self._ensure_dir()
            record = {
                "timestamp": datetime.now().isoformat(),
                "tool_name": tool_name,
                "arguments": self._sanitize(arguments),
                "result_summary": self._summarize(result),
                "confirmed": confirmed,
                "duration_ms": round(duration_ms, 2),
            }
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # 审计日志失败不应阻断主流程
            pass

    def _sanitize(self, arguments: dict) -> dict:
        """脱敏：隐藏 api_key、password 等敏感字段"""
        sensitive_keys = {"api_key", "password", "token", "secret", "key"}
        sanitized = {}
        for k, v in arguments.items():
            if any(sk in k.lower() for sk in sensitive_keys):
                sanitized[k] = "***"
            else:
                sanitized[k] = v
        return sanitized

    def _summarize(self, result: dict) -> str:
        """摘要结果"""
        if not isinstance(result, dict):
            return str(result)[:200]
        if not result.get("ok", True):
            return f"ERROR: {result.get('error', 'unknown')[:200]}"
        r = result.get("result", "")
        if isinstance(r, str):
            return r[:200]
        return json.dumps(r, ensure_ascii=False)[:200]

    def recent(self, n: int = 20) -> list[dict]:
        """读取最近的审计记录"""
        if not AUDIT_FILE.exists():
            return []
        try:
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            records = [json.loads(line) for line in lines if line.strip()]
            return records[-n:]
        except Exception:
            return []

    def stats(self) -> dict:
        """审计统计"""
        if not AUDIT_FILE.exists():
            return {"total": 0, "file": str(AUDIT_FILE)}
        try:
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                count = sum(1 for _ in f if _.strip())
            return {"total": count, "file": str(AUDIT_FILE)}
        except Exception:
            return {"total": 0, "file": str(AUDIT_FILE)}
