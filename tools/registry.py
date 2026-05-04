"""
工具注册表 — 自动发现 + 动态注册 + 优雅降级

替代 builtin.py 中的字典式注册，实现：
- ToolRegistry 单例（线程安全）
- ToolDefinition 数据类
- 自动扫描 tools/ 目录，发现含 registry.register() 的模块
- 动态 Schema：只暴露当前环境实际可用的工具
- check_fn 依赖检查 + TTL 缓存

设计参考：Hermes Agent 的 tools/registry.py
"""
import importlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
import json


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    schema: dict                    # OpenAI function calling 格式
    handler: Callable               # async 或 sync 的处理函数
    category: str = "general"       # 工具分类
    check_fn: Optional[Callable] = None  # 依赖检查函数，返回 bool
    is_async: bool = False          # handler 是否是 async
    risk_level: str = "low"         # low / medium / high
    _available: Optional[bool] = field(default=None, repr=False)
    _check_ts: float = field(default=0.0, repr=False)

    CHECK_TTL = 30  # check_fn 结果缓存秒数

    def is_available(self) -> bool:
        """检查工具是否可用（带 TTL 缓存）"""
        if self.check_fn is None:
            return True
        now = time.time()
        if self._available is not None and (now - self._check_ts) < self.CHECK_TTL:
            return self._available
        try:
            self._available = bool(self.check_fn())
        except Exception:
            self._available = False
        self._check_ts = now
        return self._available


class ToolRegistry:
    """工具注册表（线程安全单例）"""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._lock = threading.Lock()
        self._generation = 0  # 缓存失效计数器

    def register(self, name: str = None, description: str = "",
                 schema: dict = None, handler: Callable = None,
                 category: str = "general", check_fn: Callable = None,
                 is_async: bool = False, risk_level: str = "low",
                 tool_def: ToolDefinition = None) -> ToolDefinition:
        """
        注册工具。两种用法：

        1. 直接传参：
           registry.register(name="web_search", description="...", schema={...}, handler=fn)

        2. 传入 ToolDefinition：
           registry.register(tool_def=ToolDefinition(...))
        """
        if tool_def is not None:
            td = tool_def
        else:
            if not name or handler is None:
                raise ValueError("必须提供 name 和 handler")
            td = ToolDefinition(
                name=name,
                description=description or "",
                schema=schema or {},
                handler=handler,
                category=category,
                check_fn=check_fn,
                is_async=is_async,
                risk_level=risk_level,
            )
        with self._lock:
            self._tools[td.name] = td
            self._generation += 1
        return td

    def unregister(self, name: str):
        """注销工具"""
        with self._lock:
            self._tools.pop(name, None)
            self._generation += 1

    def get(self, name: str) -> Optional[ToolDefinition]:
        """获取工具定义"""
        with self._lock:
            return self._tools.get(name)

    def get_all(self) -> List[ToolDefinition]:
        """获取所有已注册工具"""
        with self._lock:
            return list(self._tools.values())

    def get_available(self) -> List[ToolDefinition]:
        """获取所有当前环境可用的工具"""
        with self._lock:
            return [td for td in self._tools.values() if td.is_available()]

    def get_schemas(self) -> List[dict]:
        """
        获取所有可用工具的 OpenAI function calling schema。
        这是传给 LLM 的工具列表 —— 只包含当前环境实际可用的工具。
        """
        available = self.get_available()
        return [
            {"type": "function", "function": td.schema}
            for td in available
        ]

    def get_names(self) -> List[str]:
        """获取所有已注册工具名"""
        with self._lock:
            return list(self._tools.keys())

    def get_available_names(self) -> List[str]:
        """获取所有可用工具名"""
        return [td.name for td in self.get_available()]

    def count(self) -> int:
        with self._lock:
            return len(self._tools)

    def available_count(self) -> int:
        return len(self.get_available())

    @property
    def generation(self) -> int:
        return self._generation

    def list_by_category(self) -> Dict[str, List[str]]:
        """按分类列出工具"""
        result = {}
        for td in self.get_available():
            result.setdefault(td.category, []).append(td.name)
        return result

    def execute(self, name: str, arguments: dict) -> str:
        """
        执行工具调用。
        返回 JSON 字符串结果。
        """
        td = self.get(name)
        if td is None:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        if not td.is_available():
            return json.dumps({"error": f"工具不可用: {name}"}, ensure_ascii=False)
        try:
            result = td.handler(**arguments)
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            return result
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ═══ 全局单例 ═══
registry = ToolRegistry()


def discover_tools(tools_dir: Path = None):
    """
    自动扫描 tools/ 目录，导入所有含 registry.register() 的模块。
    在 main.py 启动时调用一次即可。
    """
    if tools_dir is None:
        tools_dir = Path(__file__).parent

    for f in sorted(tools_dir.glob("*.py")):
        if f.name in ("__init__.py", "registry.py", "builtin_compat.py"):
            continue
        mod_name = f"tools.{f.stem}"
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            print(f"⚠️ 加载工具模块 {mod_name} 失败: {e}")
