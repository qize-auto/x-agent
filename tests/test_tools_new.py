"""
Tests for new tools: http, api_test, database, docgen
"""
import json
import sqlite3
from pathlib import Path
import pytest

from xagent.tools.http import http_request
from xagent.tools.api_test import api_test_assert, _get_json_path
from xagent.tools.database import query_sqlite
from xagent.tools.docgen import generate_api_docs, generate_changelog


class TestHttpRequest:
    def test_http_request_success(self):
        # 使用 httpbin 或类似服务进行真实测试
        # 这里用一个稳定的公开 API
        result = http_request("https://httpbin.org/get", method="GET", timeout=15)
        assert "status" in result
        assert "elapsed_ms" in result
        if result["ok"]:
            assert result["status"] == 200
            assert result["body_json"] is not None
        else:
            # 网络不稳定时允许失败，但不应抛异常
            assert "error" in result

    def test_http_request_post_json(self):
        payload = {"test": "value"}
        result = http_request(
            "https://httpbin.org/post",
            method="POST",
            json_data=payload,
            timeout=15,
        )
        if result["ok"]:
            assert result["status"] == 200
            # httpbin 会 echo 请求体
            resp_json = result.get("body_json", {})
            assert resp_json.get("json") == payload
        else:
            assert "error" in result

    def test_http_request_404(self):
        result = http_request("https://httpbin.org/status/404", timeout=15)
        assert result["status"] == 404
        assert result["ok"] is False

    def test_http_request_invalid_url(self):
        result = http_request("https://invalid.invalid.invalid", timeout=5)
        assert result["ok"] is False
        assert "error" in result


class TestApiTestAssert:
    def test_assert_status_pass(self):
        resp = {"status": 200, "body_text": "ok", "elapsed_ms": 50}
        result = api_test_assert(resp, expected_status=200)
        assert result["passed"] is True

    def test_assert_status_fail(self):
        resp = {"status": 500, "body_text": "error", "elapsed_ms": 50}
        result = api_test_assert(resp, expected_status=200)
        assert result["passed"] is False
        assert "Status" in result["message"]

    def test_assert_contains_pass(self):
        resp = {"status": 200, "body_text": "hello world", "elapsed_ms": 50}
        result = api_test_assert(resp, contains="world")
        assert result["passed"] is True

    def test_assert_contains_fail(self):
        resp = {"status": 200, "body_text": "hello world", "elapsed_ms": 50}
        result = api_test_assert(resp, contains="foo")
        assert result["passed"] is False

    def test_assert_json_path_pass(self):
        resp = {"status": 200, "body_json": {"data": {"name": "alice"}}, "body_text": "", "elapsed_ms": 50}
        result = api_test_assert(resp, json_path="data.name", expected_value="alice")
        assert result["passed"] is True

    def test_assert_json_path_fail(self):
        resp = {"status": 200, "body_json": {"data": {"name": "alice"}}, "body_text": "", "elapsed_ms": 50}
        result = api_test_assert(resp, json_path="data.name", expected_value="bob")
        assert result["passed"] is False

    def test_assert_response_time(self):
        resp = {"status": 200, "body_text": "ok", "elapsed_ms": 150}
        result = api_test_assert(resp, max_response_time_ms=100)
        assert result["passed"] is False
        result2 = api_test_assert(resp, max_response_time_ms=200)
        assert result2["passed"] is True

    def test_assert_regex_pass(self):
        resp = {"status": 200, "body_text": "User ID: 12345", "elapsed_ms": 50}
        result = api_test_assert(resp, regex=r"User ID: \d+")
        assert result["passed"] is True


class TestJsonPath:
    def test_get_json_path_dict(self):
        data = {"a": {"b": {"c": 42}}}
        assert _get_json_path(data, "a.b.c") == 42

    def test_get_json_path_list(self):
        data = {"items": [{"name": "foo"}, {"name": "bar"}]}
        assert _get_json_path(data, "items.0.name") == "foo"
        assert _get_json_path(data, "items.1.name") == "bar"

    def test_get_json_path_missing(self):
        data = {"a": 1}
        missing = _get_json_path(data, "b.c")
        # _MISSING is a sentinel object
        from xagent.tools.api_test import _MISSING
        assert missing is _MISSING


class TestDatabase:
    def test_query_sqlite_select(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('alice'), ('bob')")
        conn.commit()
        conn.close()

        result = query_sqlite(str(db), "SELECT * FROM users WHERE name = ?", params=["alice"])
        assert result["ok"] is True
        assert result["row_count"] == 1
        assert result["columns"] == ["id", "name"]
        assert result["rows"][0]["name"] == "alice"

    def test_query_sqlite_readonly_blocks_write(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()

        result = query_sqlite(str(db), "INSERT INTO t (id) VALUES (1)", readonly=True)
        assert result["ok"] is False
        assert "只读模式" in result["error"]

    def test_query_sqlite_allows_write_when_not_readonly(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()

        result = query_sqlite(str(db), "INSERT INTO t (id) VALUES (1)", readonly=False)
        assert result["ok"] is True
        assert result["row_count"] == 0  # INSERT 返回 0 行


class TestDocgen:
    def test_generate_api_docs(self, tmp_path):
        result = generate_api_docs(
            project_root=str(tmp_path),
            output_dir="docs/api",
            modules=["xagent.core", "xagent.tools"],
        )
        assert result["ok"] is True
        assert len(result["files"]) == 2
        assert (tmp_path / "docs/api" / "xagent_core.md").exists()
        assert (tmp_path / "docs/api" / "xagent_tools.md").exists()

    def test_generate_changelog(self, tmp_path):
        # 初始化 git 仓库并创建几个提交
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "a.txt").write_text("a")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add feature a"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "b.txt").write_text("b")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: fix bug b"], cwd=str(tmp_path), capture_output=True)

        result = generate_changelog(str(tmp_path), output_file="CHANGELOG.md")
        assert result["ok"] is True
        assert (tmp_path / "CHANGELOG.md").exists()
        content = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "Features" in content or "feat" in content
        assert "Bug Fixes" in content or "fix" in content
