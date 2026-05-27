"""
数据库操作工具
=============
SQLite 查询和 ChromaDB 向量搜索。
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any


def query_sqlite(db_path: str, query: str, params: list = None, readonly: bool = True) -> dict:
    """
    执行 SQLite 查询。

    Args:
        db_path: 数据库文件路径
        query: SQL 查询语句
        params: 查询参数列表（防 SQL 注入）
        readonly: 是否只读（默认 True，拒绝修改语句）

    Returns:
        {"ok": bool, "rows": list[dict], "columns": list[str], "row_count": int, "error": str|None}
    """
    params = params or []
    query_stripped = query.strip().upper()

    if readonly:
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "TRUNCATE"]
        first_word = query_stripped.split()[0] if query_stripped else ""
        if first_word in forbidden:
            return {"ok": False, "rows": [], "columns": [], "row_count": 0, "error": f"只读模式下禁止执行 {first_word}"}

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.close()
        return {
            "ok": True,
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "rows": [], "columns": [], "row_count": 0, "error": str(e)}


def vector_search(collection_name: str, query_text: str, n_results: int = 5, persist_dir: str = None) -> dict:
    """
    在 ChromaDB 集合中执行向量搜索。

    Args:
        collection_name: ChromaDB 集合名称
        query_text: 查询文本
        n_results: 返回结果数量
        persist_dir: ChromaDB 持久化目录（默认 ~/.xagent/memory/chroma）

    Returns:
        {"ok": bool, "results": list[dict], "error": str|None}
    """
    try:
        import chromadb
    except ImportError:
        return {"ok": False, "results": [], "error": "chromadb 未安装"}

    persist_dir = persist_dir or str(Path.home() / ".xagent" / "memory" / "chroma")
    try:
        client = chromadb.PersistentClient(path=persist_dir)
        collection = client.get_collection(name=collection_name)
        results = collection.query(query_texts=[query_text], n_results=n_results)

        items = []
        if results and results.get("documents"):
            for i in range(len(results["documents"][0])):
                items.append({
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                    "distance": results["distances"][0][i] if results.get("distances") else None,
                })
        return {"ok": True, "results": items, "error": None}
    except Exception as e:
        return {"ok": False, "results": [], "error": str(e)}


def register_database_tools(registry):
    registry.register(
        name="query_sqlite",
        description="执行 SQLite 查询（默认只读）。返回行数据和列名。",
        parameters={
            "type": "object",
            "properties": {
                "db_path": {"type": "string", "description": "数据库文件路径"},
                "query": {"type": "string", "description": "SQL 查询语句"},
                "params": {"type": "array", "description": "查询参数列表", "default": []},
                "readonly": {"type": "boolean", "description": "是否只读（禁止修改语句）", "default": True},
            },
            "required": ["db_path", "query"],
        },
        func=query_sqlite,
        parallel_safe=True,
    )
    registry.register(
        name="vector_search",
        description="在 ChromaDB 向量数据库中搜索语义相似的文档。",
        parameters={
            "type": "object",
            "properties": {
                "collection_name": {"type": "string", "description": "ChromaDB 集合名称"},
                "query_text": {"type": "string", "description": "查询文本"},
                "n_results": {"type": "integer", "description": "返回结果数量", "default": 5},
                "persist_dir": {"type": "string", "description": "ChromaDB 持久化目录", "default": None},
            },
            "required": ["collection_name", "query_text"],
        },
        func=vector_search,
        parallel_safe=True,
    )
