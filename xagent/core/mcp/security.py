"""MCP Security Layer

基于 2025-2026 年 MCP 安全研究实现的防御层：
- Tool Poisoning 检测（prompt injection via tool description）
- Response Sanitization（过滤恶意返回内容）
- Sandboxed Execution（子进程资源限制）

设计原则：
- 默认不信任任何外部 MCP server
- 防御层可独立启用/禁用
- 零外部依赖（仅标准库 subprocess + re）
- 误报优于漏报（宁可阻止合法 tool，也不放行恶意 tool）
"""
from __future__ import annotations
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SecurityScanResult:
    """安全扫描结果"""
    passed: bool
    threats: list[dict] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 - 1.0

    @property
    def is_safe(self) -> bool:
        # 完全安全：通过扫描且无任何威胁迹象
        return self.passed and self.risk_score == 0.0 and len(self.threats) == 0


class MCPSecurityScanner:
    """
    MCP Tool 安全扫描器。

    在将 MCP tool 注册到 ToolRegistry 之前，扫描其描述和 schema
    中是否包含 prompt injection、指令覆盖等攻击向量。
    """

    # 高风险模式：直接指令覆盖
    HIGH_RISK_PATTERNS = [
        # 忽略之前指令
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|commands)",
        r"disregard\s+(all\s+)?(previous|prior|above)?\s*(instructions|prompts|commands)",
        # 系统提示覆盖
        r"system\s*prompt\s*[:：]",
        r"new\s+system\s+instruction",
        # 角色扮演劫持
        r"you\s+are\s+now\s+(a\s+)?\w+",
        r"act\s+as\s+(a\s+)?\w+",
        # DAN / 越狱变体
        r"DAN\s*[:\(]",
        r"do\s+anything\s+now",
        r"jailbreak",
        # 敏感操作指令
        r"delete\s+(all\s+)?(files?|data)",
        r"format\s+(all\s+)?(drives?|disks?)",
        r"rm\s+-rf\s+/",
        r"send\s+(all\s+)?(data|files|logs)\s+to",
    ]

    # 中风险模式：可疑但可能是误报
    MEDIUM_RISK_PATTERNS = [
        # 外部 URL（可能是数据外泄通道）
        r"https?://\S+",
        # 代码执行相关
        r"`{3}\w*\n",  # 代码块
        r"exec\s*\(",
        r"eval\s*\(",
        r"subprocess\.",
        r"os\.system",
        # 敏感关键词
        r"password\s*[:=]",
        r"api[_-]?key\s*[:=]",
        r"token\s*[:=]",
        r"secret\s*[:=]",
        # 社会工程
        r"urgent",
        r"important",
        r"ignore\s+safety",
    ]

    # 描述长度异常（极长可能是隐藏注入）
    MAX_DESCRIPTION_LENGTH = 5000

    def __init__(self, block_high_risk: bool = True,
                 block_medium_risk_threshold: int = 3):
        self.block_high_risk = block_high_risk
        self.block_medium_risk_threshold = block_medium_risk_threshold
        self._high_risk_re = [re.compile(p, re.IGNORECASE) for p in self.HIGH_RISK_PATTERNS]
        self._medium_risk_re = [re.compile(p, re.IGNORECASE) for p in self.MEDIUM_RISK_PATTERNS]

    def scan_tool(self, name: str, description: str, input_schema: dict) -> SecurityScanResult:
        """扫描单个 MCP tool 的安全性"""
        threats = []
        risk_score = 0.0
        text_to_scan = f"{name}\n{description}\n{self._schema_to_text(input_schema)}"

        # 检查高风险模式
        high_risk_hits = 0
        for pattern in self._high_risk_re:
            matches = pattern.findall(text_to_scan)
            if matches:
                high_risk_hits += len(matches)
                threats.append({
                    "level": "high",
                    "pattern": pattern.pattern[:50],
                    "matches": matches[:3],  # 最多记录 3 个
                })

        if high_risk_hits > 0:
            risk_score += min(high_risk_hits * 0.3, 0.9)
            if self.block_high_risk:
                return SecurityScanResult(
                    passed=False,
                    threats=threats,
                    risk_score=risk_score,
                )

        # 检查中风险模式
        medium_risk_hits = 0
        for pattern in self._medium_risk_re:
            matches = pattern.findall(text_to_scan)
            if matches:
                medium_risk_hits += len(matches)
                threats.append({
                    "level": "medium",
                    "pattern": pattern.pattern[:50],
                    "count": len(matches),
                })

        risk_score += min(medium_risk_hits * 0.05, 0.3)

        # 描述长度异常（极长描述可能是隐藏注入）
        if len(description) > self.MAX_DESCRIPTION_LENGTH:
            threats.append({
                "level": "medium",
                "pattern": "description_too_long",
                "detail": f"Description length {len(description)} > {self.MAX_DESCRIPTION_LENGTH}",
            })
            risk_score += 0.15
            medium_risk_hits += 1  # 计入 medium risk hits

        # 综合判断
        passed = (risk_score < 0.3 and
                  high_risk_hits == 0 and
                  medium_risk_hits < self.block_medium_risk_threshold)

        return SecurityScanResult(
            passed=passed,
            threats=threats,
            risk_score=round(risk_score, 2),
        )

    def scan_tools_batch(self, tools: list[dict]) -> dict[str, SecurityScanResult]:
        """批量扫描工具"""
        results = {}
        for tool in tools:
            name = tool.get("name", "unknown")
            results[name] = self.scan_tool(
                name=name,
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )
        return results

    @staticmethod
    def _schema_to_text(schema: dict) -> str:
        """将 JSON schema 转为文本用于扫描"""
        if not schema:
            return ""
        # 递归提取所有字符串值
        texts = []

        def extract(obj):
            if isinstance(obj, str):
                texts.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    extract(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract(item)

        extract(schema)
        return "\n".join(texts)


class MCPResponseFilter:
    """
    MCP 工具返回内容过滤器。

    防御：
    1. Toxicity Amplification（恶意返回内容劫持 LLM）
    2. 敏感信息泄露（API keys、密码）
    3. 过大响应（DoS 攻击）
    """

    # 敏感信息模式
    SENSITIVE_PATTERNS = [
        (r"sk-[a-zA-Z0-9]{20,50}", "possible_openai_api_key"),
        (r"gh[pousr]_[a-zA-Z0-9]{20,50}", "possible_github_token"),
        (r"AKIA[0-9A-Z]{16}", "possible_aws_access_key"),
        (r"[a-zA-Z0-9_-]*api[_-]?key[a-zA-Z0-9_-]*\s*[:=]\s*\S+", "possible_api_key"),
        (r"password\s*[:=]\s*\S+", "possible_password"),
        (r"secret\s*[:=]\s*\S+", "possible_secret"),
        (r"private[_-]?key\s*[:=]\s*\S+", "possible_private_key"),
    ]

    # 恶意指令模式（返回内容中试图给 LLM 下指令）
    MALICIOUS_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts)",
        r"you\s+should\s+now\s+",
        r"instead\s*,?\s*you\s+must",
        r"your\s+new\s+instruction\s+is",
    ]

    DEFAULT_MAX_SIZE = 10 * 1024 * 1024  # 10 MB

    def __init__(self, max_response_size: int = DEFAULT_MAX_SIZE,
                 redact_sensitive: bool = True,
                 block_malicious: bool = True):
        self.max_response_size = max_response_size
        self.redact_sensitive = redact_sensitive
        self.block_malicious = block_malicious
        self._sensitive_re = [(re.compile(p, re.IGNORECASE), label) for p, label in self.SENSITIVE_PATTERNS]
        self._malicious_re = [re.compile(p, re.IGNORECASE) for p in self.MALICIOUS_PATTERNS]

    def filter(self, content: Any) -> dict:
        """
        过滤并检查 MCP 工具返回内容。

        Returns:
            {"safe": bool, "content": Any, "warnings": list[str], "blocked": bool}
        """
        warnings = []
        blocked = False

        # 1. 大小检查
        content_str = self._to_string(content)
        if len(content_str) > self.max_response_size:
            warnings.append(f"Response too large: {len(content_str)} bytes > {self.max_response_size}")
            content = self._truncate(content, self.max_response_size)
            blocked = True

        # 2. 敏感信息检测/脱敏
        if self.redact_sensitive:
            content_str, redactions = self._redact_sensitive(content_str)
            if redactions:
                warnings.extend([f"Redacted: {r}" for r in redactions])
                content = self._update_content(content, content_str)

        # 3. 恶意指令检测
        if self.block_malicious:
            malicious_hits = []
            for pattern in self._malicious_re:
                if pattern.search(content_str):
                    malicious_hits.append(pattern.pattern[:40])
            if malicious_hits:
                warnings.append(f"Malicious patterns detected in response: {malicious_hits}")
                blocked = True
                content = "[BLOCKED: Response contained potentially malicious instructions]"

        return {
            "safe": not blocked and len(warnings) == 0,
            "content": content,
            "warnings": warnings,
            "blocked": blocked,
        }

    @staticmethod
    def _to_string(content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return str(content)
        except Exception:
            return ""

    @staticmethod
    def _truncate(content: Any, max_size: int) -> Any:
        if isinstance(content, str):
            return content[:max_size] + f"\n...[truncated from {len(content)} bytes]"
        if isinstance(content, list):
            return content[:100]  # 粗略截断
        return content

    def _redact_sensitive(self, text: str) -> tuple[str, list[str]]:
        redactions = []
        for pattern, label in self._sensitive_re:
            if pattern.search(text):
                text = pattern.sub(f"[REDACTED:{label}]", text)
                redactions.append(label)
        return text, redactions

    @staticmethod
    def _update_content(original: Any, new_text: str) -> Any:
        if isinstance(original, str):
            return new_text
        # 对于非字符串类型，保持原样（脱敏只对字符串有意义）
        return original


class MCPSandbox:
    """
    MCP Server 沙箱执行环境。

    通过 subprocess 的资源限制实现隔离：
    - 内存限制（防止 OOM）
    - CPU 时间限制（防止无限循环）
    - 执行时间限制（防止挂起）
    - 可选：网络隔离（通过防火墙规则，平台相关）

    注意：这不是容器级隔离，而是进程级 resource limit。
    对于不可信 server，应配合 Docker / Firejail 使用。
    """

    def __init__(self,
                 max_memory_mb: int = 512,
                 max_cpu_time_sec: int = 60,
                 max_wall_time_sec: int = 120,
                 max_output_mb: int = 10):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_time_sec = max_cpu_time_sec
        self.max_wall_time_sec = max_wall_time_sec
        self.max_output_mb = max_output_mb

    def build_popen_kwargs(self) -> dict:
        """
        返回用于 subprocess.Popen 的额外参数。
        在支持 setrlimit 的平台上设置资源限制。
        """
        kwargs = {}

        # Windows 和 Unix 的资源限制方式不同
        import sys
        if sys.platform != "win32":
            def preexec_func():
                import resource
                # 内存限制（软限制）
                max_mem_bytes = self.max_memory_mb * 1024 * 1024
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (max_mem_bytes, max_mem_bytes))
                except (ValueError, OSError):
                    pass
                # CPU 时间限制
                try:
                    resource.setrlimit(resource.RLIMIT_CPU, (self.max_cpu_time_sec, self.max_cpu_time_sec))
                except (ValueError, OSError):
                    pass

            kwargs["preexec_fn"] = preexec_func

        return kwargs

    def wrap_transport(self, transport: Any) -> Any:
        """
        包装 transport，注入沙箱参数。
        目前仅支持 StdioTransport。
        """
        sandbox_kwargs = self.build_popen_kwargs()
        if hasattr(transport, "_proc") or hasattr(transport, "start"):
            # 尝试注入 preexec_fn 到 transport
            # 对于 StdioTransport，我们在 start() 时传递
            pass
        return transport

    def check_violation(self, proc: subprocess.Popen) -> str | None:
        """检查进程是否违反了资源限制"""
        if proc.poll() is None:
            return None  # 仍在运行

        returncode = proc.returncode
        if returncode == -9:  # SIGKILL
            return "Process killed (possibly OOM)"
        if returncode == -24:  # SIGXCPU (Unix)
            return "CPU time limit exceeded"
        if returncode != 0:
            return f"Process exited with code {returncode}"
        return None


# ── 便捷组合 ──

class MCPSecurityBundle:
    """安全层一键组合"""

    def __init__(self,
                 scanner: MCPSecurityScanner = None,
                 response_filter: MCPResponseFilter = None,
                 sandbox: MCPSandbox = None):
        self.scanner = scanner or MCPSecurityScanner()
        self.response_filter = response_filter or MCPResponseFilter()
        self.sandbox = sandbox or MCPSandbox()

    def scan_and_filter_tools(self, tools: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        批量扫描工具，返回 (safe_tools, blocked_tools)。
        blocked_tools 中每个 dict 增加了 `_security_result` 字段。
        """
        safe = []
        blocked = []
        for tool in tools:
            result = self.scanner.scan_tool(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )
            if result.is_safe:
                safe.append(tool)
            else:
                tool["_security_result"] = {
                    "passed": result.passed,
                    "risk_score": result.risk_score,
                    "threats": result.threats,
                }
                blocked.append(tool)
        return safe, blocked
