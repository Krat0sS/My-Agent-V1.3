#!/usr/bin/env python3
"""
测试 DeepSeek function calling 是否正常工作。
直接用项目的 config 和 llm 模块，发一条带工具的消息，看 LLM 会不会调用。
"""
import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from core.llm import chat

# 只给一个工具，排除干扰
TEST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "organize_directory",
            "description": "一键整理目录。自动扫描 → 按扩展名分类 → 创建分类文件夹 → 移动文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要整理的目录路径（如 ~/Desktop, ~/Downloads）"
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "预览模式：只返回分类方案，不实际移动文件",
                        "default": False
                    }
                },
                "required": ["path"]
            }
        }
    }
]

# 极简 system prompt，排除干扰
MESSAGES = [
    {
        "role": "system",
        "content": "你是一个文件整理助手。用户让你整理目录时，直接调用 organize_directory 工具。不要解释，不要建议，直接调工具。"
    },
    {
        "role": "user",
        "content": "帮我整理桌面文件"
    }
]


async def test_with_tools():
    """测试1：带工具调用"""
    print("=" * 60)
    print("测试1：带 tool_choice=auto（当前配置）")
    print(f"  model: {config.LLM_MODEL}")
    print(f"  base_url: {config.LLM_BASE_URL}")
    print(f"  tools: {len(TEST_TOOLS)} 个")
    print(f"  system prompt 长度: {len(MESSAGES[0]['content'])} 字符")
    print("=" * 60)

    result = await chat(MESSAGES, tools=TEST_TOOLS, use_ollama=False)

    has_tool_calls = "tool_calls" in result
    content_len = len(result.get("content", ""))
    print(f"\n结果:")
    print(f"  tool_calls: {has_tool_calls}")
    print(f"  content_len: {content_len}")
    if has_tool_calls:
        for tc in result["tool_calls"]:
            print(f"  → {tc['function']['name']}({tc['function']['arguments']})")
    else:
        print(f"  content: {result.get('content', '')[:300]}")

    return has_tool_calls


async def test_with_required():
    """测试2：强制 tool_choice=required"""
    print("\n" + "=" * 60)
    print("测试2：强制 tool_choice=required")
    print("=" * 60)

    # 直接调 _chat_cloud，手动设置 tool_choice
    from core.llm import get_client
    import asyncio

    client = get_client()
    kwargs = {
        "model": config.LLM_MODEL,
        "messages": MESSAGES,
        "temperature": 0.3,
        "max_tokens": 8000,
        "tools": TEST_TOOLS,
        "tool_choice": "required",  # 强制调用
    }
    if "deepseek" in config.LLM_MODEL.lower():
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=30
        )
        msg = resp.choices[0].message
        has_tool_calls = bool(msg.tool_calls)
        print(f"\n结果:")
        print(f"  tool_calls: {has_tool_calls}")
        if has_tool_calls:
            for tc in msg.tool_calls:
                print(f"  → {tc.function.name}({tc.function.arguments})")
        else:
            print(f"  content: {(msg.content or '')[:300]}")
        return has_tool_calls
    except Exception as e:
        print(f"  错误: {e}")
        return False


async def test_without_tools():
    """测试3：不带工具（对照组）"""
    print("\n" + "=" * 60)
    print("测试3：不带工具（对照组）")
    print("=" * 60)

    result = await chat(MESSAGES, tools=None, use_ollama=False)

    content_len = len(result.get("content", ""))
    print(f"\n结果:")
    print(f"  content_len: {content_len}")
    print(f"  content: {result.get('content', '')[:300]}")
    return False


async def main():
    print(f"\n🔧 DeepSeek Function Calling 诊断工具")
    print(f"   API: {config.LLM_BASE_URL}")
    print(f"   Model: {config.LLM_MODEL}\n")

    r1 = await test_with_tools()
    r2 = await test_with_required()
    await test_without_tools()

    print("\n" + "=" * 60)
    print("诊断结论:")
    print("=" * 60)
    if r1:
        print("  ✅ tool_choice=auto 正常工作 — 问题在其他地方")
    else:
        print("  ❌ tool_choice=auto 不调用工具 — 这是根因")

    if r2:
        print("  ✅ tool_choice=required 可以强制调用 — 可以用这个绕过")
    else:
        print("  ❌ tool_choice=required 也不行 — DeepSeek function calling 有严重问题")

    if not r1 and r2:
        print("\n  💡 建议：把 tool_choice 从 'auto' 改为 'required'")
        print("     文件：core/llm.py → _chat_cloud()")

    if not r1 and not r2:
        print("\n  💡 DeepSeek function calling 完全不工作，可能需要：")
        print("     1. 检查 API 版本是否支持 function calling")
        print("     2. 换模型（如 deepseek-reasoner）")
        print("     3. 检查 tools schema 格式")


if __name__ == "__main__":
    asyncio.run(main())
