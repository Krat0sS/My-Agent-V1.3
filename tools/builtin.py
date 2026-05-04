"""工具注册与执行框架"""
import json
import os
import re
import glob as glob_mod
import difflib
import time
import hashlib
import shutil
import datetime
from typing import Callable
import config


# ═══ 工具注册表 ═══
_tools: dict[str, dict] = {}  # name -> {"func": callable, "schema": dict, "enabled": bool}


# ═══ 工具结果缓存 ═══
_cache: dict[str, tuple[float, str]] = {}  # key -> (expire_timestamp, result)


def _cache_key(func_name: str, args: dict) -> str:
    """生成缓存键：函数名 + 参数哈希"""
    raw = f"{func_name}:{json.dumps(args, sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(func_name: str, args: dict) -> str | None:
    """查询缓存，未命中或过期返回 None"""
    if config.TOOL_CACHE_TTL <= 0:
        return None
    key = _cache_key(func_name, args)
    entry = _cache.get(key)
    if entry is None:
        return None
    expire_ts, result = entry
    if time.time() > expire_ts:
        del _cache[key]
        return None
    # 在结果前加缓存标记
    try:
        parsed = json.loads(result)
        parsed["_cached"] = True
        return json.dumps(parsed, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return result


def _cache_set(func_name: str, args: dict, result: str):
    """写入缓存"""
    if config.TOOL_CACHE_TTL <= 0:
        return
    key = _cache_key(func_name, args)
    _cache[key] = (time.time() + config.TOOL_CACHE_TTL, result)


# 可缓存的工具列表（只读操作）
CACHEABLE_TOOLS = {"read_file", "list_files", "browser_navigate", "browser_screenshot", "recall", "list_windows", "get_active_window"}


def register(name: str, description: str, parameters: dict, enabled: bool = True):
    """装饰器：注册工具"""
    def decorator(func: Callable):
        _tools[name] = {
            "func": func,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            },
            "enabled": enabled
        }
        return func
    return decorator


def get_tool_schemas() -> list[dict]:
    """获取所有启用工具的 schema（传给 LLM）"""
    return [t["schema"] for t in _tools.values() if t["enabled"]]


def execute(name: str, arguments: dict) -> str:
    """执行工具调用（带缓存）"""
    tool = _tools.get(name)
    if not tool:
        return json.dumps({"error": f"未知工具: {name}"})
    if not tool["enabled"]:
        return json.dumps({"error": f"工具已禁用: {name}"})

    # 查询缓存（仅对只读工具）
    if name in CACHEABLE_TOOLS:
        cached = _cache_get(name, arguments)
        if cached is not None:
            return cached

    try:
        result = tool["func"](**arguments)
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)

        # 写入缓存（仅对只读工具且无错误）
        if name in CACHEABLE_TOOLS:
            try:
                parsed = json.loads(result)
                if not (isinstance(parsed, dict) and "error" in parsed):
                    _cache_set(name, arguments, result)
            except (json.JSONDecodeError, TypeError):
                pass

        return result
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_tools() -> list[str]:
    """列出所有工具名"""
    return list(_tools.keys())


# ═══ 内置工具 ═══

def _structured_error(error_type: str, message: str, hint: str = "",
                      recoverable: bool = False, **extra) -> str:
    """
    生成结构化错误响应。
    Agent 拿到这个 JSON 后能自动判断：是否可恢复、该怎么向用户解释。
    """
    result = {
        "error": True,
        "type": error_type,
        "message": message,
        "recoverable": recoverable,
        "display_hint": hint or message,
    }
    result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def _classify_os_error(e: OSError, path: str) -> str:
    """将 OSError 分类为用户友好的结构化错误"""
    errno = getattr(e, 'errno', None)
    msg = str(e).lower()

    if errno == 13 or 'permission' in msg or 'access' in msg:
        return _structured_error(
            "permission_denied", f"没有权限访问: {path}",
            hint="文件可能被其他程序占用，或你没有访问权限。关闭占用该文件的程序后重试。",
            recoverable=True, path=path
        )
    elif errno == 28 or 'no space' in msg or 'disk' in msg:
        return _structured_error(
            "disk_full", f"磁盘空间不足，无法写入: {path}",
            hint="磁盘满了。清理一些文件后重试。可以用 find_files 找大文件删除。",
            recoverable=True, path=path
        )
    elif errno == 36 or 'file name' in msg or 'too long' in msg:
        return _structured_error(
            "filename_too_long", f"文件名过长: {path}",
            hint="文件名超过系统限制（通常 255 字符）。请缩短文件名。",
            recoverable=True, path=path
        )
    elif errno == 18 or 'cross-device' in msg or 'invalid' in msg:
        return _structured_error(
            "cross_device", f"跨设备移动失败: {path}",
            hint="源和目标不在同一个磁盘分区。将使用复制+删除方式重试。",
            recoverable=True, path=path
        )
    else:
        return _structured_error(
            "os_error", f"文件操作失败: {e}",
            hint=f"操作系统错误。路径: {path}",
            recoverable=False, path=path, errno=errno
        )

@register(
    name="read_file",
    description="读取文件内容",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（绝对或相对）"}
        },
        "required": ["path"]
    }
)
def read_file(path: str) -> str:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return _structured_error("not_found", f"文件不存在: {path}",
                                hint="检查路径是否正确，或用 find_files 搜索文件名。",
                                recoverable=True, path=path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return json.dumps({"path": path, "size": len(content), "content": content[:50000]})
    except PermissionError:
        return _structured_error("permission_denied", f"没有权限读取: {path}",
                                hint="文件可能被其他程序占用，或权限不足。",
                                recoverable=True, path=path)
    except UnicodeDecodeError:
        return _structured_error("encoding_error", f"无法以 UTF-8 读取: {path}",
                                hint="文件可能是二进制文件或使用了其他编码。试试用 run_command 读取。",
                                recoverable=True, path=path)
    except Exception as e:
        return _classify_os_error(e, path) if isinstance(e, OSError) else _structured_error(
            "read_failed", f"读取失败: {e}", recoverable=False, path=path)


@register(
    name="write_file",
    description="创建或写入文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "文件内容"}
        },
        "required": ["path", "content"]
    }
)
def write_file(path: str, content: str) -> str:
    path = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"success": True, "path": path, "bytes": len(content)})
    except PermissionError:
        return _structured_error("permission_denied", f"没有权限写入: {path}",
                                hint="目标目录可能受保护，或文件被其他程序占用。",
                                recoverable=True, path=path)
    except OSError as e:
        return _classify_os_error(e, path)
    except Exception as e:
        return _structured_error("write_failed", f"写入失败: {e}",
                                recoverable=False, path=path)


@register(
    name="edit_file",
    description="精确编辑文件（查找替换）",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_text": {"type": "string", "description": "要替换的原文"},
            "new_text": {"type": "string", "description": "替换后的内容"}
        },
        "required": ["path", "old_text", "new_text"]
    }
)
def edit_file(path: str, old_text: str, new_text: str) -> str:
    path = os.path.expanduser(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if old_text not in content:
        return json.dumps({"error": "未找到要替换的文本"})
    content = content.replace(old_text, new_text, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return json.dumps({"success": True, "path": path})


@register(
    name="list_files",
    description="列出目录下的文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径", "default": "."},
            "pattern": {"type": "string", "description": "文件模式（如 *.py）", "default": "*"}
        }
    }
)
def list_files(path: str = ".", pattern: str = "*") -> str:
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return json.dumps({"error": f"目录不存在: {path}"})
    files = []
    for f in glob_mod.glob(os.path.join(path, pattern)):
        rel = os.path.relpath(f, path)
        is_dir = os.path.isdir(f)
        size = 0 if is_dir else os.path.getsize(f)
        files.append({"name": rel, "is_dir": is_dir, "size": size})
    return json.dumps({"path": path, "files": files[:100]})


@register(
    name="run_command",
    description="执行 shell 命令。对于危险命令（rm, chmod, pip install, git push 等）会要求用户确认。支持超时和取消。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "cwd": {"type": "string", "description": "工作目录", "default": None},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 30}
        },
        "required": ["command"]
    }
)
def run_command(command: str, cwd: str = None, timeout: int = 30) -> str:
    """同步包装器 — 由 conversation.py 通过 run_in_executor 调用"""
    from tools.subprocess_runner import run_command_async
    import asyncio
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, run_command_async(command, cwd, timeout))
            return future.result(timeout=timeout + 5)
    except RuntimeError:
        return asyncio.run(run_command_async(command, cwd, timeout))


@register(
    name="run_command_confirmed",
    description="执行已确认的危险命令（跳过确认检查）。仅在用户明确同意后使用。支持超时和取消。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令（已确认）"},
            "cwd": {"type": "string", "description": "工作目录", "default": None},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 30}
        },
        "required": ["command"]
    }
)
def run_command_confirmed(command: str, cwd: str = None, timeout: int = 30) -> str:
    """同步包装器"""
    from tools.subprocess_runner import run_command_confirmed_async
    import asyncio
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, run_command_confirmed_async(command, cwd, timeout))
            return future.result(timeout=timeout + 5)
    except RuntimeError:
        return asyncio.run(run_command_confirmed_async(command, cwd, timeout))


@register(
    name="web_search",
    description="真实联网搜索（DuckDuckGo）。返回搜索结果摘要和链接。适用于查找最新信息、文档、教程、新闻等。不要用你的知识猜测——用这个工具获取真实数据。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，建议用英文关键词效果更好"},
            "objective": {"type": "string", "description": "你希望从搜索结果中提取什么（如'找代码示例'、'查最新版本号'）", "default": ""},
            "max_results": {"type": "integer", "description": "最大结果数", "default": 5}
        },
        "required": ["query"]
    }
)
def web_search(query: str, objective: str = "", max_results: int = 5) -> str:
    """真实联网搜索（DuckDuckGo）"""
    from tools.search import search_and_summarize_sync
    result = search_and_summarize_sync(query, max_results=max_results, objective=objective)
    return json.dumps(result, ensure_ascii=False)


@register(
    name="news_search",
    description="搜索新闻。用于查找最新事件、行业动态、产品发布等时效性信息。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "新闻搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数", "default": 5}
        },
        "required": ["query"]
    }
)
def news_search(query: str, max_results: int = 5) -> str:
    """搜索新闻"""
    from tools.search import news_search_sync
    result = news_search_sync(query, max_results=max_results)
    return json.dumps(result, ensure_ascii=False)


@register(
    name="remember",
    description="保存重要信息到长期记忆",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容"}
        },
        "required": ["content"]
    }
)
def remember(content: str) -> str:
    """写入 MEMORY.md"""
    import datetime
    os.makedirs(os.path.dirname(config.MEMORY_FILE), exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n\n### {timestamp}\n{content}\n"
    with open(config.MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    return json.dumps({"success": True, "message": "已保存到长期记忆"})


@register(
    name="recall",
    description="检索长期记忆（语义模糊匹配）",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要回忆的内容关键词或描述"}
        },
        "required": ["query"]
    }
)
def recall(query: str) -> str:
    """
    检索长期记忆。
    使用 jieba 分词 + 模糊匹配，提升中文语义检索精度。
    """
    if not os.path.exists(config.MEMORY_FILE):
        return json.dumps({"result": "暂无长期记忆"})

    with open(config.MEMORY_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        return json.dumps({"result": "暂无长期记忆"})

    # 按 ### 标题切分段落
    sections = []
    current = ""
    for line in content.split("\n"):
        if line.startswith("### ") and current.strip():
            sections.append(current.strip())
            current = line + "\n"
        else:
            current += line
    if current.strip():
        sections.append(current.strip())

    if not sections:
        return json.dumps({"result": content[-3000:]})

    # jieba 分词
    try:
        import jieba
        def tokenize(text: str) -> list[str]:
            """jieba 分词 + 过滤停用词"""
            words = jieba.lcut(text.lower())
            # 过滤：单字符、纯数字、纯标点
            return [w for w in words if len(w) > 1 and not w.isdigit() and not re.match(r'^[\s\W]+$', w)]

        query_tokens = tokenize(query)
    except ImportError:
        # jieba 不可用，回退到简单切词
        def tokenize(text: str) -> list[str]:
            return [w for w in text.lower().split() if len(w) > 1]
        query_tokens = tokenize(query)

    # 模糊匹配：jieba 分词 + SequenceMatcher + 关键词加权
    query_lower = query.lower()
    scored = []
    for section in sections:
        section_lower = section.lower()
        section_tokens = tokenize(section)

        # 1. SequenceMatcher 相似度
        seq_score = difflib.SequenceMatcher(None, query_lower, section_lower).ratio()

        # 2. jieba 词级命中率
        if query_tokens and section_tokens:
            section_set = set(section_tokens)
            token_hits = sum(1 for t in query_tokens if t in section_set)
            token_score = token_hits / len(query_tokens)
        else:
            token_score = 0

        # 3. 原始关键词命中（兜底）
        keyword_hits = sum(1 for kw in query_lower.split() if kw in section_lower)
        keyword_score = keyword_hits / max(len(query_lower.split()), 1)

        # 综合评分：jieba 命中权重最高
        total_score = seq_score * 0.2 + token_score * 0.5 + keyword_score * 0.3
        scored.append((total_score, section))

    scored.sort(reverse=True, key=lambda x: x[0])

    # 返回 top-k 段落
    results = []
    total_len = 0
    for score, section in scored[:5]:
        if score < 0.05:  # 相关度过低，跳过
            continue
        if total_len + len(section) > 4000:
            break
        results.append(section)
        total_len += len(section)

    return json.dumps({
        "query": query,
        "query_tokens": query_tokens if 'query_tokens' in dir() else query.split(),
        "matches": len(results),
        "result": "\n\n---\n\n".join(results) if results else "未找到相关记忆。"
    })


@register(
    name="set_preference",
    description="设置用户偏好参数（如 verbosity, style 等），会持久化并在后续对话中生效",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "参数名（如 verbosity, style, detail_level）"},
            "value": {"type": "string", "description": "参数值"}
        },
        "required": ["key", "value"]
    }
)
def set_preference(key: str, value: str) -> str:
    """更新可学习参数"""
    from memory.memory_system import MemorySystem
    memory = MemorySystem()
    old_value = memory.learned_params.get(key, "(未设置)")
    memory.update_param(key, value)
    return json.dumps({
        "success": True,
        "key": key,
        "old_value": old_value,
        "new_value": value,
        "message": f"已更新偏好：{key} = {value}"
    })


# ═══ 文件助手工具 ═══

# 常见文件扩展名 → 分类映射
_EXT_CATEGORIES = {
    # 文档
    ".doc": "文档", ".docx": "文档", ".pdf": "文档", ".txt": "文档",
    ".rtf": "文档", ".odt": "文档", ".md": "文档", ".tex": "文档",
    ".xls": "文档", ".xlsx": "文档", ".csv": "文档", ".ppt": "文档",
    ".pptx": "文档", ".pages": "文档", ".numbers": "文档", ".key": "文档",
    # 代码
    ".py": "代码", ".js": "代码", ".ts": "代码", ".java": "代码",
    ".c": "代码", ".cpp": "代码", ".h": "代码", ".cs": "代码",
    ".go": "代码", ".rs": "代码", ".rb": "代码", ".php": "代码",
    ".swift": "代码", ".kt": "代码", ".scala": "代码", ".r": "代码",
    ".m": "代码", ".sh": "代码", ".bat": "代码", ".ps1": "代码",
    ".html": "代码", ".css": "代码", ".scss": "代码", ".vue": "代码",
    ".jsx": "代码", ".tsx": "代码", ".json": "代码", ".xml": "代码",
    ".yaml": "代码", ".yml": "代码", ".toml": "代码", ".ini": "代码",
    ".sql": "代码", ".db": "代码", ".sqlite": "代码",
    # 图片
    ".jpg": "图片", ".jpeg": "图片", ".png": "图片", ".gif": "图片",
    ".bmp": "图片", ".svg": "图片", ".webp": "图片", ".ico": "图片",
    ".tiff": "图片", ".tif": "图片", ".heic": "图片", ".heif": "图片",
    ".psd": "图片", ".ai": "图片", ".eps": "图片", ".raw": "图片",
    # 视频
    ".mp4": "视频", ".avi": "视频", ".mkv": "视频", ".mov": "视频",
    ".wmv": "视频", ".flv": "视频", ".webm": "视频", ".m4v": "视频",
    ".mpg": "视频", ".mpeg": "视频", ".3gp": "视频",
    # 音频
    ".mp3": "音频", ".wav": "音频", ".flac": "音频", ".aac": "音频",
    ".ogg": "音频", ".wma": "音频", ".m4a": "音频", ".opus": "音频",
    ".mid": "音频", ".midi": "音频",
    # 压缩包
    ".zip": "压缩包", ".rar": "压缩包", ".7z": "压缩包", ".tar": "压缩包",
    ".gz": "压缩包", ".bz2": "压缩包", ".xz": "压缩包", ".tgz": "压缩包",
    # 可执行/安装
    ".exe": "程序", ".msi": "程序", ".dmg": "程序", ".app": "程序",
    ".deb": "程序", ".rpm": "程序", ".apk": "程序", ".ipa": "程序",
    ".jar": "程序", ".war": "程序",
    # 种子/下载
    ".torrent": "下载", ".metalink": "下载",
}


def _categorize_file(filename: str) -> str:
    """根据扩展名自动分类文件"""
    ext = os.path.splitext(filename)[1].lower()
    return _EXT_CATEGORIES.get(ext, "其他")


@register(
    name="scan_files",
    description="扫描目录，返回带元数据的文件列表。每个文件包含名称、大小、修改时间、扩展名和自动分类。用于了解目录结构和文件分布。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要扫描的目录路径", "default": "."},
            "recursive": {"type": "boolean", "description": "是否递归扫描子目录", "default": False},
            "include_hidden": {"type": "boolean", "description": "是否包含隐藏文件（以.开头）", "default": False}
        }
    }
)
def scan_files(path: str = ".", recursive: bool = False, include_hidden: bool = False) -> str:
    path = os.path.expanduser(path)

    # 路径修正：~/Desktop 在 Windows 上可能不存在
    if not os.path.isdir(path):
        if "Desktop" in path or "desktop" in path:
            alt = _get_special_folder("Desktop")
            if os.path.isdir(alt):
                path = alt
        elif "Downloads" in path or "downloads" in path:
            alt = _get_special_folder("Downloads")
            if os.path.isdir(alt):
                path = alt

    if not os.path.isdir(path):
        return json.dumps({"error": f"目录不存在: {path}"})

    files = []
    categories = {}

    if recursive:
        for root, dirs, filenames in os.walk(path):
            if not include_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                filenames = [f for f in filenames if not f.startswith(".")]
            for fname in filenames:
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, path)
                try:
                    stat = os.stat(fpath)
                    ext = os.path.splitext(fname)[1].lower()
                    cat = _categorize_file(fname)
                    files.append({
                        "name": fname,
                        "path": rel_path,
                        "ext": ext,
                        "size": stat.st_size,
                        "size_human": _human_size_py(stat.st_size),
                        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "category": cat,
                    })
                    categories[cat] = categories.get(cat, 0) + 1
                except OSError:
                    continue
    else:
        try:
            entries = sorted(os.listdir(path))
        except OSError as e:
            return json.dumps({"error": str(e)})

        for fname in entries:
            if not include_hidden and fname.startswith("."):
                continue
            fpath = os.path.join(path, fname)
            try:
                stat = os.stat(fpath)
                ext = os.path.splitext(fname)[1].lower()
                cat = _categorize_file(fname)
                is_dir = os.path.isdir(fpath)
                files.append({
                    "name": fname,
                    "is_dir": is_dir,
                    "ext": ext if not is_dir else "",
                    "size": 0 if is_dir else stat.st_size,
                    "size_human": "📁" if is_dir else _human_size_py(stat.st_size),
                    "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "category": "目录" if is_dir else cat,
                })
                if not is_dir:
                    categories[cat] = categories.get(cat, 0) + 1
            except OSError:
                continue

    return json.dumps({
        "path": path,
        "total": len(files),
        "categories": categories,
        "files": files[:200],
    }, ensure_ascii=False)


@register(
    name="move_file",
    description="移动文件或目录到目标位置。自动记录回滚点，用户可随时恢复。如果目标已存在同名文件，返回冲突信息而不是覆盖。",
    parameters={
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "源文件路径"},
            "dst": {"type": "string", "description": "目标路径（可以是目录或完整文件名）"},
            "op_id": {"type": "string", "description": "回滚操作组 ID（由 start_rollback 返回），不传则自动创建", "default": None}
        },
        "required": ["src", "dst"]
    }
)
def move_file(src: str, dst: str, op_id: str = None) -> str:
    from tools import rollback

    src = os.path.expanduser(src)
    dst = os.path.expanduser(dst)

    if not os.path.exists(src):
        return _structured_error("not_found", f"源文件不存在: {src}",
                                hint="文件可能已被移动或删除。用 find_files 搜索一下？",
                                recoverable=True, path=src)

    # 如果 dst 是目录，拼接文件名
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))

    # 检查冲突 — 返回详细对比信息，而非简单报错
    if os.path.exists(dst):
        try:
            src_stat = os.stat(src)
            dst_stat = os.stat(dst)
            src_mtime = datetime.datetime.fromtimestamp(src_stat.st_mtime).isoformat()
            dst_mtime = datetime.datetime.fromtimestamp(dst_stat.st_mtime).isoformat()
            return json.dumps({
                "error": "conflict",
                "message": f"目标已存在同名文件",
                "src": src,
                "dst": dst,
                "src_info": {
                    "size": src_stat.st_size,
                    "size_human": _human_size_py(src_stat.st_size),
                    "modified": src_mtime,
                    "category": _categorize_file(os.path.basename(src)),
                },
                "dst_info": {
                    "size": dst_stat.st_size,
                    "size_human": _human_size_py(dst_stat.st_size),
                    "modified": dst_mtime,
                    "category": _categorize_file(os.path.basename(dst)),
                },
                "options": [
                    "skip — 跳过此文件",
                    "rename — 自动重命名（在文件名后加序号）",
                    "overwrite — 覆盖目标文件（不可恢复）",
                ],
                "hint": "请告诉用户两个文件的大小和日期对比，让用户选择处理方式。默认建议「两个都保留（重命名）」。",
            }, ensure_ascii=False)
        except OSError:
            return json.dumps({
                "error": "conflict",
                "message": f"目标已存在同名文件: {dst}",
                "src": src,
                "dst": dst,
            }, ensure_ascii=False)

    # 记录回滚
    if op_id is None:
        op_id = rollback.start_operation("自动文件操作")
    entry = rollback.record_move(src, dst)

    # 执行移动
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        rollback.add_entry(op_id, entry)
        return json.dumps({
            "success": True,
            "src": src,
            "dst": dst,
            "op_id": op_id,
            "category": _categorize_file(os.path.basename(dst)),
        }, ensure_ascii=False)
    except OSError as e:
        # 跨设备移动失败 → 自动用复制+删除重试
        if getattr(e, 'errno', None) == 18 or 'cross-device' in str(e).lower():
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                    shutil.rmtree(src)
                else:
                    shutil.copy2(src, dst)
                    os.remove(src)
                rollback.add_entry(op_id, entry)
                return json.dumps({
                    "success": True,
                    "src": src,
                    "dst": dst,
                    "op_id": op_id,
                    "method": "copy+delete (跨设备自动重试)",
                    "category": _categorize_file(os.path.basename(dst)),
                }, ensure_ascii=False)
            except Exception as e2:
                return _classify_os_error(e2, src)
        return _classify_os_error(e, src)
    except Exception as e:
        return _structured_error("move_failed", f"移动失败: {e}",
                                recoverable=False, src=src, dst=dst)


@register(
    name="batch_move",
    description="批量移动文件。传入文件映射列表，一次操作完成所有移动。自动归为同一个回滚操作组，支持一键全部撤销。",
    parameters={
        "type": "object",
        "properties": {
            "moves": {
                "type": "array",
                "description": "移动映射列表，每项包含 src 和 dst",
                "items": {
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "源路径"},
                        "dst": {"type": "string", "description": "目标路径"}
                    },
                    "required": ["src", "dst"]
                }
            },
            "description": {"type": "string", "description": "操作描述（用于回滚列表展示）", "default": "批量文件移动"}
        },
        "required": ["moves"]
    }
)
def batch_move(moves: list, description: str = "批量文件移动") -> str:
    from tools import rollback

    op_id = rollback.start_operation(description)
    results = []
    success_count = 0
    error_count = 0

    for m in moves:
        src = os.path.expanduser(m.get("src", ""))
        dst = os.path.expanduser(m.get("dst", ""))

        if not src or not dst:
            results.append({"src": src, "dst": dst, "status": "error", "reason": "路径为空"})
            error_count += 1
            continue

        if not os.path.exists(src):
            results.append({"src": src, "dst": dst, "status": "error", "reason": "源文件不存在"})
            error_count += 1
            continue

        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src))

        if os.path.exists(dst):
            results.append({"src": src, "dst": dst, "status": "conflict", "reason": "目标已存在"})
            error_count += 1
            continue

        entry = rollback.record_move(src, dst)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            rollback.add_entry(op_id, entry)
            results.append({"src": src, "dst": dst, "status": "moved", "category": _categorize_file(os.path.basename(dst))})
            success_count += 1
        except OSError as e:
            # 跨设备自动重试
            if getattr(e, 'errno', None) == 18 or 'cross-device' in str(e).lower():
                try:
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                        shutil.rmtree(src)
                    else:
                        shutil.copy2(src, dst)
                        os.remove(src)
                    rollback.add_entry(op_id, entry)
                    results.append({"src": src, "dst": dst, "status": "moved", "method": "copy+delete", "category": _categorize_file(os.path.basename(dst))})
                    success_count += 1
                    continue
                except Exception:
                    pass
            results.append({"src": src, "dst": dst, "status": "error", "reason": str(e), "recoverable": True})
            error_count += 1
        except Exception as e:
            results.append({"src": src, "dst": dst, "status": "error", "reason": str(e), "recoverable": False})
            error_count += 1

    rollback.complete_operation(op_id)

    return json.dumps({
        "op_id": op_id,
        "total": len(moves),
        "success": success_count,
        "errors": error_count,
        "results": results,
        "rollback_hint": f"如果需要撤销，告诉我「回滚 {op_id}」",
    }, ensure_ascii=False)


@register(
    name="rollback_operation",
    description="回滚之前的文件操作。可以回滚最近一次操作，或指定操作 ID。恢复所有被移动的文件到原始位置。",
    parameters={
        "type": "object",
        "properties": {
            "op_id": {"type": "string", "description": "要回滚的操作 ID。不传则回滚最近一次操作。", "default": None}
        }
    }
)
def rollback_operation(op_id: str = None) -> str:
    from tools import rollback

    if op_id is None:
        # 回滚最近一次操作
        ops = rollback.list_operations()
        if not ops:
            return json.dumps({"error": "没有可回滚的操作记录"})
        op_id = ops[0]["op_id"]

    result = rollback.rollback(op_id)
    # Q5: 优先使用信任审计消息
    if "user_message" in result:
        result["display_message"] = result["user_message"]
    return json.dumps(result, ensure_ascii=False)


@register(
    name="list_rollback_history",
    description="列出所有回滚操作历史记录。查看之前做过哪些文件操作，以及是否已回滚。",
    parameters={"type": "object", "properties": {}}
)
def list_rollback_history() -> str:
    from tools import rollback
    ops = rollback.list_operations(include_rolled_back=True)
    return json.dumps({
        "total": len(ops),
        "operations": ops
    }, ensure_ascii=False)


@register(
    name="find_files",
    description="搜索文件。支持按名称（模糊匹配）、扩展名、日期范围、大小范围搜索。返回按相关度排序的结果列表。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索起始目录", "default": "."},
            "name": {"type": "string", "description": "文件名关键词（模糊匹配）", "default": ""},
            "ext": {"type": "string", "description": "文件扩展名过滤（如 .py, .pdf）", "default": ""},
            "modified_after": {"type": "string", "description": "只返回此日期之后修改的文件（格式 YYYY-MM-DD）", "default": ""},
            "modified_before": {"type": "string", "description": "只返回此日期之前修改的文件（格式 YYYY-MM-DD）", "default": ""},
            "min_size": {"type": "integer", "description": "最小文件大小（字节）", "default": 0},
            "max_size": {"type": "integer", "description": "最大文件大小（字节，0=不限）", "default": 0},
            "max_results": {"type": "integer", "description": "最大返回数量", "default": 50}
        }
    }
)
def find_files(path: str = ".", name: str = "", ext: str = "",
               modified_after: str = "", modified_before: str = "",
               min_size: int = 0, max_size: int = 0, max_results: int = 50) -> str:
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return json.dumps({"error": f"目录不存在: {path}"})

    # 解析日期
    dt_after = None
    dt_before = None
    if modified_after:
        try:
            dt_after = datetime.datetime.strptime(modified_after, "%Y-%m-%d")
        except ValueError:
            pass
    if modified_before:
        try:
            dt_before = datetime.datetime.strptime(modified_before, "%Y-%m-%d")
        except ValueError:
            pass

    results = []
    name_lower = name.lower()
    ext_lower = ext.lower() if ext else ""

    for root, dirs, filenames in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue

            # 扩展名过滤
            fext = os.path.splitext(fname)[1].lower()
            if ext_lower and fext != ext_lower:
                continue

            # 名称过滤
            if name_lower and name_lower not in fname.lower():
                continue

            fpath = os.path.join(root, fname)
            try:
                stat = os.stat(fpath)
            except OSError:
                continue

            # 日期过滤
            mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
            if dt_after and mtime < dt_after:
                continue
            if dt_before and mtime > dt_before:
                continue

            # 大小过滤
            if min_size and stat.st_size < min_size:
                continue
            if max_size and stat.st_size > max_size:
                continue

            rel_path = os.path.relpath(fpath, path)
            cat = _categorize_file(fname)
            results.append({
                "name": fname,
                "path": rel_path,
                "full_path": fpath,
                "ext": fext,
                "size": stat.st_size,
                "size_human": _human_size_py(stat.st_size),
                "modified": mtime.isoformat(),
                "category": cat,
            })

    # 按修改时间倒序（最新的在前）
    results.sort(key=lambda x: x["modified"], reverse=True)
    results = results[:max_results]

    return json.dumps({
        "path": path,
        "query": {"name": name, "ext": ext, "after": modified_after, "before": modified_before},
        "total": len(results),
        "results": results,
    }, ensure_ascii=False)


@register(
    name="organize_directory",
    description="一键整理目录。自动扫描 → 按扩展名分类 → 创建分类文件夹 → 移动文件。整个操作自动归为同一个回滚组，用户说「恢复」即可一键撤销。这是文件助手的核心入口工具。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": f"要整理的目录路径（如 {_get_special_folder('Desktop')}, {_get_special_folder('Downloads')}）"},
            "dry_run": {"type": "boolean", "description": "预览模式：只返回分类方案，不实际移动文件", "default": False},
            "exclude": {
                "type": "array",
                "description": "排除的文件名列表（不移动这些文件）",
                "items": {"type": "string"},
                "default": []
            },
            "custom_categories": {
                "type": "object",
                "description": "自定义分类覆盖：{\"文件名关键词\": \"目标文件夹名\"}。如 {\"截图\": \"截图\", \"周报\": \"报告\"}",
                "default": {}
            }
        },
        "required": ["path"]
    }
)
def organize_directory(path: str, dry_run: bool = False, exclude: list = None,
                       custom_categories: dict = None) -> str:
    from tools import rollback

    path = os.path.expanduser(path)

    # 路径修正：~/Desktop 在 Windows 上可能不存在，尝试跨平台查找
    if not os.path.isdir(path):
        if "Desktop" in path or "desktop" in path:
            alt = _get_special_folder("Desktop")
            if os.path.isdir(alt):
                path = alt
        elif "Downloads" in path or "downloads" in path:
            alt = _get_special_folder("Downloads")
            if os.path.isdir(alt):
                path = alt

    if not os.path.isdir(path):
        return json.dumps({"error": f"目录不存在: {path}"})

    exclude = set(exclude or [])
    custom_categories = custom_categories or {}

    # 扫描文件
    try:
        entries = os.listdir(path)
    except OSError as e:
        return json.dumps({"error": str(e)})

    files = []
    dirs_already = []
    for fname in entries:
        if fname.startswith(".") or fname in exclude:
            continue
        fpath = os.path.join(path, fname)
        if os.path.isdir(fpath):
            dirs_already.append(fname)
            continue
        if os.path.isfile(fpath):
            files.append((fname, fpath))

    if not files:
        return json.dumps({
            "message": "目录已经是空的或只有文件夹，无需整理",
            "path": path,
            "existing_dirs": dirs_already,
        })

    # 分类逻辑：先检查自定义关键词，再用扩展名映射
    categories = {}  # category -> [(fname, fpath)]
    uncertain = []

    for fname, fpath in files:
        cat = None

        # 1. 自定义关键词匹配
        if custom_categories:
            for keyword, target_cat in custom_categories.items():
                if keyword.lower() in fname.lower():
                    cat = target_cat
                    break

        # 2. 扩展名映射
        if cat is None:
            cat = _categorize_file(fname)

        # 3. "其他" 归为不确定
        if cat == "其他":
            uncertain.append((fname, fpath))
        else:
            categories.setdefault(cat, []).append((fname, fpath))

    # 构建分类摘要
    summary = {}
    for cat, items in categories.items():
        summary[cat] = {
            "count": len(items),
            "files": [f for f, _ in items[:10]],
            "extra": len(items) - 10 if len(items) > 10 else 0,
        }
    if uncertain:
        summary["⚠️ 不确定"] = {
            "count": len(uncertain),
            "files": [f for f, _ in uncertain[:10]],
            "extra": len(uncertain) - 10 if len(uncertain) > 10 else 0,
            "hint": "这些文件无法自动分类，请用户决定如何处理",
        }

    # 预览模式：只返回方案，不执行
    if dry_run:
        return json.dumps({
            "mode": "preview",
            "path": path,
            "total_files": len(files),
            "categories": summary,
            "message": f"将整理 {len(files)} 个文件到 {len(categories)} 个分类文件夹"
                       + (f"，{len(uncertain)} 个文件无法分类" if uncertain else ""),
        }, ensure_ascii=False)

    # 执行模式：创建文件夹 + 移动文件
    op_id = rollback.start_operation(f"整理目录: {path}")
    moved_count = 0
    skipped = []
    move_errors = []

    for cat, items in categories.items():
        cat_dir = os.path.join(path, cat)
        try:
            os.makedirs(cat_dir, exist_ok=True)
            rollback.add_entry(op_id, rollback.record_create(cat_dir))
        except OSError as e:
            move_errors.append({"category": cat, "error": str(e)})
            continue

        for fname, fpath in items:
            dst = os.path.join(cat_dir, fname)
            if os.path.exists(dst):
                # 冲突：自动重命名
                base, ext = os.path.splitext(fname)
                counter = 1
                while os.path.exists(dst):
                    dst = os.path.join(cat_dir, f"{base}_{counter}{ext}")
                    counter += 1
                skipped.append({"file": fname, "reason": f"同名已存在，重命名为 {os.path.basename(dst)}"})

            entry = rollback.record_move(fpath, dst)
            try:
                shutil.move(fpath, dst)
                rollback.add_entry(op_id, entry)
                moved_count += 1
            except Exception as e:
                move_errors.append({"file": fname, "error": str(e)})

    rollback.complete_operation(op_id)

    # 标记已整理（重置提醒计时器）
    try:
        from tools.file_monitor import mark_cleanup, DEFAULT_WATCH_DIRS
        for dir_path, dir_label in DEFAULT_WATCH_DIRS:
            if os.path.abspath(os.path.expanduser(dir_path)) == os.path.abspath(path):
                mark_cleanup(dir_label)
                break
        else:
            # 不在默认监控列表中，用路径末尾作为标签
            mark_cleanup(os.path.basename(path))
    except Exception:
        pass

    return json.dumps({
        "success": True,
        "op_id": op_id,
        "path": path,
        "total_files": len(files),
        "moved": moved_count,
        "categories": {cat: len(items) for cat, items in categories.items()},
        "uncertain": len(uncertain),
        "uncertain_files": [f for f, _ in uncertain[:20]],
        "skipped": skipped[:10],
        "errors": move_errors[:10],
        "rollback_hint": f"说「恢复 {op_id}」可一键撤销本次整理",
        "display_hint": "告诉用户：已整理完成，列出各分类文件数量，提示不确定的文件留给用户处理，并说明如何撤销。",
    }, ensure_ascii=False)


@register(
    name="check_directory_status",
    description="检查桌面和下载文件夹的文件状态。返回各目录文件数量、大小、是否需要整理。用于心跳检查或主动整理提醒。",
    parameters={"type": "object", "properties": {}}
)
def check_directory_status() -> str:
    from tools.file_monitor import check_all
    result = check_all()
    # 标记已提醒的目录
    if result.get("needs_remind", 0) > 0:
        from tools.file_monitor import mark_reminded
        for d in result.get("remind_dirs", []):
            mark_reminded(d.get("dir", ""))
    return json.dumps(result, ensure_ascii=False)


@register(
    name="get_new_files",
    description="获取指定目录最近新增的文件（默认 24 小时内）。用于检测新下载的文件并判断是否需要整理。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径"},
            "hours": {"type": "integer", "description": "检查最近多少小时内的新增文件", "default": 24}
        }
    }
)
def get_new_files(path: str = None, hours: int = 24) -> str:
    from tools.file_monitor import get_new_files as _get_new
    if path is None:
        path = _get_special_folder("Downloads")
    result = _get_new(path, hours)
    return json.dumps({
        "path": path,
        "hours": hours,
        "new_count": len(result),
        "files": result,
    }, ensure_ascii=False)


@register(
    name="mark_cleanup_done",
    description="标记某个目录刚刚整理过。用于重置提醒计时器，避免重复提醒。",
    parameters={
        "type": "object",
        "properties": {
            "dir_label": {"type": "string", "description": "目录标签（如 '桌面', '下载'）"}
        },
        "required": ["dir_label"]
    }
)
def mark_cleanup_done(dir_label: str) -> str:
    from tools.file_monitor import mark_cleanup
    mark_cleanup(dir_label)
    return json.dumps({"success": True, "message": f"已标记「{dir_label}」为已整理，7 天内不再提醒"})


def _get_special_folder(folder_name: str) -> str:
    """获取跨平台特殊目录（桌面、下载等）"""
    import platform
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            if folder_name == "Desktop":
                path = winreg.QueryValueEx(key, "Desktop")[0]
            elif folder_name == "Downloads":
                # Downloads 没有注册表项，用常见的默认路径
                path = os.path.join(os.path.expanduser("~"), "Downloads")
            else:
                path = os.path.expanduser(f"~/{folder_name}")
            winreg.CloseKey(key)
            if os.path.exists(path):
                return path
        except Exception:
            pass
    return os.path.expanduser(f"~/{folder_name}")


def _human_size_py(size_bytes: int) -> str:
    """人类可读的文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


# ═══ 浏览器工具 ═══

@register(
    name="browser_navigate",
    description="打开网页并获取页面文本内容。适用于查看 GitHub 仓库、文档、文章等。自动清洗和压缩长页面。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网页完整地址（如 https://github.com/user/repo）"},
            "objective": {"type": "string", "description": "你希望从页面中提取什么信息，用于内容摘要优化", "default": "提取页面主要文本内容"}
        },
        "required": ["url"]
    }
)
def browser_navigate(url: str, objective: str = "提取页面主要文本内容") -> str:
    """打开 URL 并返回清洗后的页面文本"""
    try:
        from tools.browser import browser_navigate as _nav
        return _nav(url, objective)
    except ImportError:
        return json.dumps({"error": "浏览器工具未安装。请运行: pip install playwright && playwright install chromium"})


@register(
    name="browser_screenshot",
    description="对指定网页截图，返回 base64 编码的图片。用于需要视觉理解的场景（如分析页面布局、验证码等）。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要截图的网页地址"},
            "full_page": {"type": "boolean", "description": "是否截取整个页面，默认 True", "default": True}
        },
        "required": ["url"]
    }
)
def browser_screenshot(url: str, full_page: bool = True) -> str:
    """截取网页截图"""
    try:
        from tools.browser import browser_screenshot as _ss
        return _ss(url, full_page)
    except ImportError:
        return json.dumps({"error": "浏览器工具未安装。请运行: pip install playwright && playwright install chromium"})


# ═══ 桌面 GUI 自动化工具 ═══

@register(
    name="list_windows",
    description="列出当前所有可见窗口。用于了解桌面状态，找到要操作的目标窗口。",
    parameters={"type": "object", "properties": {}}
)
def list_windows() -> str:
    from tools.desktop import list_windows as _fn
    return _fn()


@register(
    name="get_active_window",
    description="获取当前活动（前台）窗口的标题和位置信息。",
    parameters={"type": "object", "properties": {}}
)
def get_active_window() -> str:
    from tools.desktop import get_active_window as _fn
    return _fn()


@register(
    name="activate_window",
    description="根据窗口标题关键词切换到前台。模糊匹配，包含关键词的窗口都会被激活。",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题关键词（如 '微信', 'Chrome', 'VSCode'）"}
        },
        "required": ["title"]
    }
)
def activate_window(title: str) -> str:
    from tools.desktop import activate_window as _fn
    return _fn(title)


@register(
    name="desktop_click",
    description="在屏幕指定坐标处点击鼠标。用于点击按钮、菜单等 UI 元素。建议先 screenshot 查看屏幕布局再点击。",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "屏幕 X 坐标"},
            "y": {"type": "integer", "description": "屏幕 Y 坐标"},
            "button": {"type": "string", "description": "鼠标按钮: left/right/middle", "default": "left"}
        },
        "required": ["x", "y"]
    }
)
def desktop_click(x: int, y: int, button: str = "left") -> str:
    from tools.desktop import click as _fn
    return _fn(x, y, button)


@register(
    name="desktop_double_click",
    description="在屏幕指定坐标处双击。用于打开文件、选中文字等。",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "屏幕 X 坐标"},
            "y": {"type": "integer", "description": "屏幕 Y 坐标"}
        },
        "required": ["x", "y"]
    }
)
def desktop_double_click(x: int, y: int) -> str:
    from tools.desktop import double_click as _fn
    return _fn(x, y)


@register(
    name="desktop_type",
    description="模拟键盘输入文本。用于在输入框中打字、发送消息等。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文本"},
            "interval": {"type": "number", "description": "每个字符间隔秒数", "default": 0.05}
        },
        "required": ["text"]
    }
)
def desktop_type(text: str, interval: float = 0.05) -> str:
    from tools.desktop import type_text as _fn
    return _fn(text, interval)


@register(
    name="desktop_keys",
    description="按下组合键。格式用 + 连接，如 'ctrl+c', 'alt+tab', 'ctrl+shift+s', 'enter'。支持: ctrl, alt, shift, win, enter, tab, esc, space, backspace, delete, up, down, left, right",
    parameters={
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "组合键，如 'ctrl+v', 'alt+F4', 'enter'"}
        },
        "required": ["keys"]
    }
)
def desktop_keys(keys: str) -> str:
    from tools.desktop import press_keys as _fn
    return _fn(keys)


@register(
    name="desktop_screenshot",
    description="截取屏幕截图，返回 base64 图片。用于查看当前桌面状态，确认点击位置等。可指定区域（x,y,width,height）。",
    parameters={
        "type": "object",
        "properties": {
            "region": {"type": "string", "description": "截图区域: 'x,y,width,height'（如 '0,0,800,600'），为空则全屏", "default": None}
        }
    }
)
def desktop_screenshot(region: str = None) -> str:
    from tools.desktop import screenshot as _fn
    return _fn(region)


@register(
    name="desktop_move_mouse",
    description="移动鼠标到指定屏幕坐标。",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "目标 X 坐标"},
            "y": {"type": "integer", "description": "目标 Y 坐标"}
        },
        "required": ["x", "y"]
    }
)
def desktop_move_mouse(x: int, y: int) -> str:
    from tools.desktop import mouse_move as _fn
    return _fn(x, y)


@register(
    name="desktop_scroll",
    description="滚动鼠标滚轮。正数向上，负数向下。可指定滚动位置。",
    parameters={
        "type": "object",
        "properties": {
            "clicks": {"type": "integer", "description": "滚动量：正数向上，负数向下（如 -3 = 向下滚 3 格）"},
            "x": {"type": "integer", "description": "可选，滚动位置 X"},
            "y": {"type": "integer", "description": "可选，滚动位置 Y"}
        },
        "required": ["clicks"]
    }
)
def desktop_scroll(clicks: int, x: int = None, y: int = None) -> str:
    from tools.desktop import scroll as _fn
    return _fn(clicks, x, y)


@register(
    name="desktop_screenshot_grid",
    description="截取带网格坐标的桌面截图。网格帮助精确定位 UI 元素位置。返回的坐标格式为 row,col。用于需要精确点击的场景。",
    parameters={
        "type": "object",
        "properties": {
            "region": {"type": "string", "description": "可选，截取区域 x,y,width,height"}
        }
    }
)
def desktop_screenshot_grid(region: str = None) -> str:
    from tools.desktop import screenshot as _fn
    return _fn(region, with_grid=True)


@register(
    name="vision_analyze",
    description="用视觉模型分析截图，识别界面元素和位置。传入 base64 图片，返回元素列表和建议操作。需要配置支持 vision 的模型。",
    parameters={
        "type": "object",
        "properties": {
            "base64_image": {"type": "string", "description": "base64 编码的图片"},
            "question": {"type": "string", "description": "分析问题（默认：识别所有可交互元素）"}
        },
        "required": ["base64_image"]
    }
)
def vision_analyze(base64_image: str, question: str = None) -> str:
    from tools.vision import analyze_screenshot_sync
    result = analyze_screenshot_sync(base64_image, question)
    return json.dumps(result, ensure_ascii=False)


@register(
    name="task_plan",
    description="分析复杂指令，生成分步执行计划。用于多步骤任务（如'打开微信找到XX发消息'）。返回结构化步骤列表。",
    parameters={
        "type": "object",
        "properties": {
            "instruction": {"type": "string", "description": "用户的完整指令"},
            "context": {"type": "string", "description": "当前环境上下文（如当前窗口、已知信息）"}
        },
        "required": ["instruction"]
    }
)
def task_plan(instruction: str, context: str = "") -> str:
    from tools.planner import plan_task, format_plan
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, plan_task(instruction, context))
            plan = future.result(timeout=30)
    except RuntimeError:
        plan = asyncio.run(plan_task(instruction, context))
    return json.dumps(plan, ensure_ascii=False)


# ═══ 浏览器会话工具（有状态，跨调用复用） ═══

@register(
    name="browser_click",
    description="在当前浏览器页面中点击元素。需要先用 browser_navigate 打开页面。selector 可以是 CSS 选择器、文本内容或 XPath。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "要点击的元素选择器（如 'a:text(\"README\")', 'button:has-text(\"Submit\")', '#login-btn', 'text=文件传输助手'）"}
        },
        "required": ["selector"]
    }
)
def browser_click(selector: str) -> str:
    """点击浏览器页面元素 — 由 conversation.py 直接 async 调用"""
    return json.dumps({"error": "browser_click 应通过对话会话调用，不应直接调用"})


@register(
    name="browser_type",
    description="在当前浏览器页面的输入框中填写文本。需要先用 browser_navigate 打开页面。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "输入框的 CSS 选择器（如 '#search-input', 'input[name=\"q\"]'）"},
            "text": {"type": "string", "description": "要输入的文本"},
            "press_enter": {"type": "boolean", "description": "输入后是否按回车", "default": False}
        },
        "required": ["selector", "text"]
    }
)
def browser_type(selector: str, text: str, press_enter: bool = False) -> str:
    return json.dumps({"error": "browser_type 应通过对话会话调用"})


@register(
    name="browser_press_key",
    description="在当前浏览器页面按下键盘按键。如 'Enter', 'Tab', 'Escape', 'ctrl+a'。",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "按键名（如 'Enter', 'Tab', 'Escape', 'ctrl+a'）"}
        },
        "required": ["key"]
    }
)
def browser_press_key(key: str) -> str:
    return json.dumps({"error": "browser_press_key 应通过对话会话调用"})


@register(
    name="browser_download",
    description="下载当前页面上的文件链接。导航到下载 URL 并保存到本地。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "文件下载链接"},
            "save_dir": {"type": "string", "description": "保存目录（默认 ~/Downloads）", "default": None}
        },
        "required": ["url"]
    }
)
def browser_download(url: str, save_dir: str = None) -> str:
    return json.dumps({"error": "browser_download 应通过对话会话调用"})


@register(
    name="browser_session_screenshot",
    description="截取当前浏览器页面的截图。需要先用 browser_navigate 打开页面。返回 base64 图片。",
    parameters={
        "type": "object",
        "properties": {
            "full_page": {"type": "boolean", "description": "是否截取整个页面（含滚动区域）", "default": True}
        }
    }
)
def browser_session_screenshot(full_page: bool = True) -> str:
    return json.dumps({"error": "browser_session_screenshot 应通过对话会话调用"})


@register(
    name="browser_get_content",
    description="获取当前浏览器页面的文本内容。用于查看页面变化后的最新内容。",
    parameters={"type": "object", "properties": {}}
)
def browser_get_content() -> str:
    return json.dumps({"error": "browser_get_content 应通过对话会话调用"})


@register(
    name="browser_wait",
    description="等待页面中指定元素出现。用于等待动态加载的内容。selector 可以是 CSS 选择器或文本选择器。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "要等待的元素选择器（如 '#result', 'text=加载完成'）"},
            "timeout": {"type": "integer", "description": "超时毫秒数", "default": 10000}
        },
        "required": ["selector"]
    }
)
def browser_wait(selector: str, timeout: int = 10000) -> str:
    return json.dumps({"error": "browser_wait 应通过对话会话调用"})


# ═══ RAG 知识库工具（延迟导入，避免循环依赖）═══
try:
    import kb_tools
except ImportError:
    pass  # kb_tools 不存在时不影响其他工具
