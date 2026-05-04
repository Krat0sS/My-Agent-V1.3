#!/usr/bin/env python3
"""
测试：用项目的完整 system prompt + tools，看 LLM 会不会调工具。
"""
import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from core.llm import chat
from tools.registry import registry
from memory.memory_system import MemorySystem
from security.context_sanitizer import get_security_prompt


async def test_full_prompt():
    """用完整 system prompt + 全部工具测试"""
    # 初始化 registry
    import tools.builtin_compat  # noqa: F401

    # 构建完整 system prompt（和 Conversation._init_system 一样）
    memory = MemorySystem()
    system_prompt = memory.get_system_prompt()
    system_prompt += "\n\n" + get_security_prompt()

    tools_schema = registry.get_schemas()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "帮我整理桌面文件"},
    ]

    print("=" * 60)
    print("测试：完整 system prompt + 全部工具")
    print(f"  model: {config.LLM_MODEL}")
    print(f"  system prompt 长度: {len(system_prompt)} 字符")
    print(f"  tools 数量: {len(tools_schema)}")
    print("=" * 60)

    result = await chat(messages, tools=tools_schema, use_ollama=False)

    has_tool_calls = "tool_calls" in result
    content_len = len(result.get("content", ""))
    print(f"\n结果:")
    print(f"  tool_calls: {has_tool_calls}")
    print(f"  content_len: {content_len}")
    if has_tool_calls:
        for tc in result["tool_calls"]:
            print(f"  → {tc['function']['name']}({tc['function']['arguments'][:100]})")
    else:
        print(f"  content 前 500 字: {result.get('content', '')[:500]}")

    return has_tool_calls


async def test_short_prompt_full_tools():
    """极简 prompt + 全部工具"""
    import tools.builtin_compat  # noqa: F401

    tools_schema = registry.get_schemas()

    messages = [
        {"role": "system", "content": "你是文件整理助手。用户让你整理目录时，直接调用 organize_directory 工具。"},
        {"role": "user", "content": "帮我整理桌面文件"},
    ]

    print("\n" + "=" * 60)
    print("测试：极简 prompt + 全部工具")
    print(f"  system prompt 长度: {len(messages[0]['content'])} 字符")
    print(f"  tools 数量: {len(tools_schema)}")
    print("=" * 60)

    result = await chat(messages, tools=tools_schema, use_ollama=False)

    has_tool_calls = "tool_calls" in result
    content_len = len(result.get("content", ""))
    print(f"\n结果:")
    print(f"  tool_calls: {has_tool_calls}")
    print(f"  content_len: {content_len}")
    if has_tool_calls:
        for tc in result["tool_calls"]:
            print(f"  → {tc['function']['name']}({tc['function']['arguments'][:100]})")
    else:
        print(f"  content 前 500 字: {result.get('content', '')[:500]}")

    return has_tool_calls


async def test_medium_prompt():
    """中等长度 prompt + 全部工具"""
    import tools.builtin_compat  # noqa: F401

    tools_schema = registry.get_schemas()

    messages = [
        {"role": "system", "content": (
            "你是 My-Agent，一个智能助手。你拥有以下工具可以操作电脑：\n"
            "- organize_directory: 一键整理目录\n"
            "- desktop_screenshot: 截取屏幕截图\n"
            "- web_search: 联网搜索\n"
            "- run_command: 执行系统命令\n\n"
            "当用户要求你整理文件或桌面时，直接调用 organize_directory 工具，不要解释。"
        )},
        {"role": "user", "content": "帮我整理桌面文件"},
    ]

    print("\n" + "=" * 60)
    print("测试：中等 prompt + 全部工具")
    print(f"  system prompt 长度: {len(messages[0]['content'])} 字符")
    print(f"  tools 数量: {len(tools_schema)}")
    print("=" * 60)

    result = await chat(messages, tools=tools_schema, use_ollama=False)

    has_tool_calls = "tool_calls" in result
    content_len = len(result.get("content", ""))
    print(f"\n结果:")
    print(f"  tool_calls: {has_tool_calls}")
    print(f"  content_len: {content_len}")
    if has_tool_calls:
        for tc in result["tool_calls"]:
            print(f"  → {tc['function']['name']}({tc['function']['arguments'][:100]})")
    else:
        print(f"  content 前 500 字: {result.get('content', '')[:500]}")

    return has_tool_calls


async def main():
    print(f"\n🔬 System Prompt 长度 vs Function Calling 诊断")
    print(f"   API: {config.LLM_BASE_URL}")
    print(f"   Model: {config.LLM_MODEL}\n")

    r1 = await test_full_prompt()
    r2 = await test_short_prompt_full_tools()
    r3 = await test_medium_prompt()

    print("\n" + "=" * 60)
    print("诊断结论:")
    print("=" * 60)
    if r1:
        print("  ✅ 完整 prompt + 全部工具 → 正常调用")
        print("     问题不在 prompt 长度，可能是路由层没有把请求送到 LLM")
    else:
        print("  ❌ 完整 prompt + 全部工具 → 不调用")
        if r2:
            print("  ✅ 极简 prompt + 全部工具 → 正常调用")
            print("     💡 根因确认：system prompt 太长导致 LLM 忽略 tools")
        else:
            print("  ❌ 极简 prompt + 全部工具 → 也不调用")
            print("     💡 问题在 tools schema 格式或工具数量过多")

    if r3:
        print("  ✅ 中等 prompt + 全部工具 → 正常调用")
    else:
        print("  ❌ 中等 prompt + 全部工具 → 不调用")


if __name__ == "__main__":
    asyncio.run(main())
