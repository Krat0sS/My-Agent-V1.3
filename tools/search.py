"""
网页搜索工具 — 基于 DuckDuckGo 真实搜索
替代原来的"假搜索"（让 LLM 编答案）

依赖：pip install duckduckgo-search

提供两种接口：
- 同步：real_search(), search_and_summarize_sync(), news_search_sync()
- 异步：async_search_and_summarize(), async_news_search()
"""
import json
import re
import asyncio


def _clean_html(text: str) -> str:
    """去除 HTML 标签"""
    return re.sub(r'<[^>]+>', '', text).strip()


def _clean_snippet(text: str) -> str:
    """清洗搜索结果片段"""
    text = _clean_html(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def real_search(query: str, max_results: int = 5) -> dict:
    """使用 DuckDuckGo 进行真实网页搜索（同步）"""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return {
            "error": True,
            "query": query,
            "message": "duckduckgo-search 未安装。运行: pip install duckduckgo-search",
            "fix": "pip install duckduckgo-search"
        }

    try:
        with DDGS() as ddgs:
            results = []
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": _clean_snippet(r.get("title", "")),
                    "url": r.get("href", ""),
                    "snippet": _clean_snippet(r.get("body", "")),
                })

        return {
            "query": query,
            "count": len(results),
            "results": results,
            "summary": "\n\n".join(
                f"**{i+1}. {r['title']}**\n{r['snippet']}\n{r['url']}"
                for i, r in enumerate(results)
            ) if results else "未找到相关结果。"
        }
    except Exception as e:
        return {
            "error": True,
            "query": query,
            "message": f"搜索失败: {str(e)}",
            "hint": "可能是网络问题或 DuckDuckGo 限流，稍后重试。"
        }


def news_search_sync(query: str, max_results: int = 5) -> dict:
    """搜索新闻（同步）"""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return {"error": True, "message": "duckduckgo-search 未安装"}

    try:
        with DDGS() as ddgs:
            results = []
            for r in ddgs.news(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": _clean_snippet(r.get("body", "")),
                    "date": r.get("date", ""),
                    "source": r.get("source", ""),
                })

        return {
            "query": query,
            "count": len(results),
            "results": results,
            "summary": "\n\n".join(
                f"**{r['title']}** ({r['source']}, {r['date']})\n{r['snippet']}\n{r['url']}"
                for r in results
            ) if results else "未找到相关新闻。"
        }
    except Exception as e:
        return {"error": True, "message": f"新闻搜索失败: {str(e)}"}


def _llm_summarize_sync(text: str, query: str, objective: str) -> str:
    """同步 LLM 摘要"""
    from core.llm import chat_simple_sync
    prompt = (
        f"用户搜索了：{query}\n"
        f"{'目标：' + objective if objective else '请提炼搜索结果的关键信息。'}\n\n"
        f"搜索结果：\n{text[:4000]}\n\n"
        f"要求：\n"
        f"1. 提取与查询最相关的关键信息\n"
        f"2. 保留重要链接和代码片段\n"
        f"3. 去除重复和无关内容\n"
        f"4. 输出结构化、易读的摘要"
    )
    return chat_simple_sync(
        "你是一个信息整理助手，擅长从搜索结果中提炼关键信息。请用中文回答。",
        prompt
    )


def search_and_summarize_sync(query: str, max_results: int = 5, objective: str = "") -> dict:
    """搜索 + LLM 摘要（同步版本，供 builtin.py 使用）"""
    search_result = real_search(query, max_results)

    if search_result.get("error"):
        return search_result

    if not search_result.get("results"):
        return {"query": query, "count": 0, "summary": "未找到相关结果。建议换个关键词试试。"}

    if len(search_result["results"]) <= 2 and not objective:
        return {"query": query, "count": search_result["count"], "summary": search_result["summary"]}

    try:
        summary = _llm_summarize_sync(search_result["summary"], query, objective)
        return {
            "query": query,
            "count": search_result["count"],
            "summary": summary,
            "raw_results": search_result["results"]
        }
    except Exception:
        return {"query": query, "count": search_result["count"], "summary": search_result["summary"]}


# ═══ 异步接口（供直接 async 调用） ═══

async def async_search_and_summarize(query: str, max_results: int = 5, objective: str = "") -> dict:
    """搜索 + LLM 摘要（异步版本）"""
    loop = asyncio.get_running_loop()
    search_result = await loop.run_in_executor(None, real_search, query, max_results)

    if search_result.get("error"):
        return search_result

    if not search_result.get("results"):
        return {"query": query, "count": 0, "summary": "未找到相关结果。"}

    if len(search_result["results"]) <= 2 and not objective:
        return {"query": query, "count": search_result["count"], "summary": search_result["summary"]}

    try:
        from core.llm import chat_simple
        prompt = (
            f"用户搜索了：{query}\n"
            f"{'目标：' + objective if objective else '请提炼搜索结果的关键信息。'}\n\n"
            f"搜索结果：\n{search_result['summary'][:4000]}\n\n"
            f"要求：提取关键信息、保留链接和代码、去重、结构化输出"
        )
        summary = await chat_simple(
            "你是一个信息整理助手，擅长从搜索结果中提炼关键信息。请用中文回答。",
            prompt
        )
        return {
            "query": query,
            "count": search_result["count"],
            "summary": summary,
            "raw_results": search_result["results"]
        }
    except Exception:
        return {"query": query, "count": search_result["count"], "summary": search_result["summary"]}


async def async_news_search(query: str, max_results: int = 5) -> dict:
    """搜索新闻（异步版本）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, news_search_sync, query, max_results)
