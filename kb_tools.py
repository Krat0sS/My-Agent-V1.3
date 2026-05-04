"""RAG 知识库工具 — 注册到 builtin.py 工具注册表

使用方法：在 builtin.py 顶部添加 `import tools.kb_tools` 即可自动注册 6 个工具。
"""

import os
import json
from tools.builtin import register, _structured_error
from knowledge_base import get_kb


@register(
    name="kb_add_file",
    description="将单个文件导入知识库。支持 .txt .md .py .js .json .csv .html 等 40+ 种格式。导入后可通过 kb_search 语义检索。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（绝对或相对）"
            }
        },
        "required": ["path"]
    }
)
def kb_add_file(path: str) -> str:
    """导入单个文件到知识库"""
    try:
        kb = get_kb()
        result = kb.add_file(os.path.expanduser(path))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _structured_error("kb_error", f"导入失败: {e}", recoverable=False)


@register(
    name="kb_add_directory",
    description="批量导入目录下的所有支持文件到知识库。可选递归扫描子目录。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径"
            },
            "recursive": {
                "type": "boolean",
                "description": "是否递归扫描子目录",
                "default": True
            }
        },
        "required": ["path"]
    }
)
def kb_add_directory(path: str, recursive: bool = True) -> str:
    """批量导入目录到知识库"""
    try:
        kb = get_kb()
        result = kb.add_directory(os.path.expanduser(path), recursive)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _structured_error("kb_error", f"批量导入失败: {e}", recoverable=False)


@register(
    name="kb_search",
    description="语义搜索知识库。输入自然语言问题，返回最相关的文档片段。用于回答用户基于已导入文档的问题。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询（自然语言）"
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果数量",
                "default": 5
            },
            "min_score": {
                "type": "number",
                "description": "最低相关度分数（0-1）",
                "default": 0.3
            }
        },
        "required": ["query"]
    }
)
def kb_search(query: str, top_k: int = 5, min_score: float = 0.3) -> str:
    """语义搜索知识库"""
    try:
        kb = get_kb()
        results = kb.search(query, top_k, min_score)
        if not results:
            return json.dumps({
                "results": [],
                "message": "未找到相关内容。知识库可能为空，或查询与文档内容不匹配。"
            }, ensure_ascii=False)

        return json.dumps({
            "results": results,
            "total": len(results),
        }, ensure_ascii=False)
    except Exception as e:
        return _structured_error("kb_error", f"搜索失败: {e}", recoverable=False)


@register(
    name="kb_remove_file",
    description="从知识库中删除指定文件的所有数据。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要删除的文件路径"
            }
        },
        "required": ["path"]
    }
)
def kb_remove_file(path: str) -> str:
    """从知识库删除文件"""
    try:
        kb = get_kb()
        result = kb.remove_file(os.path.expanduser(path))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _structured_error("kb_error", f"删除失败: {e}", recoverable=False)


@register(
    name="kb_stats",
    description="查看知识库统计信息：总分块数、来源文件数、嵌入后端、各文件分块数量等。",
    parameters={
        "type": "object",
        "properties": {}
    }
)
def kb_stats() -> str:
    """查看知识库统计"""
    try:
        kb = get_kb()
        stats = kb.stats()
        return json.dumps(stats, ensure_ascii=False)
    except Exception as e:
        return _structured_error("kb_error", f"获取统计失败: {e}", recoverable=False)


@register(
    name="kb_clear",
    description="清空整个知识库。此操作不可逆。",
    parameters={
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "确认清空，必须为 true"
            }
        },
        "required": ["confirm"]
    }
)
def kb_clear(confirm: bool = False) -> str:
    """清空知识库"""
    if not confirm:
        return json.dumps({
            "error": "需要确认。设置 confirm=true 来清空知识库。"
        }, ensure_ascii=False)
    try:
        kb = get_kb()
        kb.clear()
        return json.dumps({"success": True, "message": "知识库已清空。"}, ensure_ascii=False)
    except Exception as e:
        return _structured_error("kb_error", f"清空失败: {e}", recoverable=False)
