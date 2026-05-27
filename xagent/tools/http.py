"""
HTTP 请求工具
============
支持 GET/POST/PUT/DELETE/PATCH，返回结构化响应。
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Any


def http_request(
    url: str,
    method: str = "GET",
    headers: dict = None,
    body: str = "",
    json_data: dict = None,
    timeout: int = 30,
) -> dict:
    """
    发送 HTTP 请求。

    Args:
        url: 目标 URL
        method: HTTP 方法 (GET/POST/PUT/DELETE/PATCH)
        headers: 请求头字典
        body: 原始请求体字符串
        json_data: JSON 请求体（会自动序列化并设置 Content-Type: application/json）
        timeout: 超时秒数

    Returns:
        {
            "status": int,
            "reason": str,
            "headers": dict,
            "body_text": str,
            "body_json": dict | None,
            "elapsed_ms": float,
            "ok": bool,
        }
    """
    headers = headers or {}
    if json_data is not None:
        body = json.dumps(json_data, ensure_ascii=False)
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(
        url,
        data=body.encode("utf-8") if body else None,
        headers=headers,
        method=method.upper(),
    )

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.time() - start) * 1000
            resp_body = resp.read().decode("utf-8", errors="replace")
            resp_headers = dict(resp.headers)
            body_json = None
            if resp_body:
                try:
                    body_json = json.loads(resp_body)
                except json.JSONDecodeError:
                    pass
            return {
                "status": resp.status,
                "reason": resp.reason,
                "headers": resp_headers,
                "body_text": resp_body,
                "body_json": body_json,
                "elapsed_ms": round(elapsed_ms, 2),
                "ok": 200 <= resp.status < 300,
            }
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - start) * 1000
        resp_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        body_json = None
        if resp_body:
            try:
                body_json = json.loads(resp_body)
            except json.JSONDecodeError:
                pass
        return {
            "status": e.code,
            "reason": e.reason,
            "headers": dict(e.headers) if e.headers else {},
            "body_text": resp_body,
            "body_json": body_json,
            "elapsed_ms": round(elapsed_ms, 2),
            "ok": False,
            "error": f"HTTPError {e.code}: {e.reason}",
        }
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        return {
            "status": 0,
            "reason": "",
            "headers": {},
            "body_text": "",
            "body_json": None,
            "elapsed_ms": round(elapsed_ms, 2),
            "ok": False,
            "error": str(e),
        }


def register_http_tools(registry):
    registry.register(
        name="http_request",
        description="发送 HTTP 请求（GET/POST/PUT/DELETE），返回状态码、响应头和体。支持 JSON 请求体。",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标 URL"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "default": "GET"},
                "headers": {"type": "object", "description": "请求头键值对", "default": {}},
                "body": {"type": "string", "description": "原始请求体", "default": ""},
                "json_data": {"type": "object", "description": "JSON 请求体（自动设置 Content-Type）", "default": None},
                "timeout": {"type": "integer", "description": "超时秒数", "default": 30},
            },
            "required": ["url"],
        },
        func=http_request,
        parallel_safe=True,
    )
