"""
X-Agent 工具集
=============
"""
from .filesystem import register_filesystem_tools
from .shell import register_shell_tools
from .web import register_web_tools
from .git_tool import register_git_tools
from .code_quality import register_code_quality_tools
from .browser import register_browser_tools
from .http import register_http_tools
from .api_test import register_api_test_tools
from .database import register_database_tools
from .docgen import register_docgen_tools


def register_all_tools(registry, project_root: str = "."):
    """注册所有内置工具到注册表"""
    register_filesystem_tools(registry, project_root=project_root)
    register_shell_tools(registry)
    register_web_tools(registry)
    register_git_tools(registry)
    register_code_quality_tools(registry)
    register_browser_tools(registry)
    register_http_tools(registry)
    register_api_test_tools(registry)
    register_database_tools(registry)
    register_docgen_tools(registry)
