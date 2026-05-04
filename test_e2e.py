#!/usr/bin/env python3
"""
端到端测试：模拟 Conversation.send("帮我整理桌面文件") 的完整流程。
逐步打印每个环节的状态，找到断点。
"""
import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import tools.builtin_compat  # noqa: F401
from tools.registry import registry
from core.intent_router import route
from skills.loader import load_all_skills
from skills.executor import SkillExecutor


async def main():
    user_input = "帮我整理桌面文件"
    print(f"{'='*60}")
    print(f"端到端测试: {user_input}")
    print(f"{'='*60}\n")

    # Step 1: 检查 registry 状态
    all_tools = registry.get_all()
    available = registry.get_available()
    schemas = registry.get_schemas()
    print(f"[1] ToolRegistry 状态:")
    print(f"    注册工具: {len(all_tools)}")
    print(f"    可用工具: {len(available)}")
    print(f"    Schema 数: {len(schemas)}")
    print(f"    工具列表: {[t.name for t in available[:10]]}...")
    print()

    # 检查 organize_directory 是否在 registry 里
    org_dir = registry.get("organize_directory")
    print(f"    organize_directory: {'✅ 存在' if org_dir else '❌ 不存在'}")
    if org_dir:
        print(f"    可用: {org_dir.is_available()}")
    scan_files = registry.get("scan_files")
    print(f"    scan_files: {'✅ 存在' if scan_files else '❌ 不存在'}")
    print()

    # Step 2: 路由
    skills = load_all_skills()
    routing = await route(user_input, skills)
    print(f"[2] 路由结果:")
    print(f"    complexity: {routing.complexity}")
    print(f"    action: {routing.action}")
    print(f"    matched_skill: {routing.matched_skill.name if routing.matched_skill else None}")
    print(f"    match_score: {routing.match_score:.3f}")
    print()

    if routing.action != "execute_skill" or not routing.matched_skill:
        print("    ❌ 路由未命中技能，流程中断")
        return

    # Step 3: SkillExecutor 执行
    skill = routing.matched_skill
    print(f"[3] 技能详情:")
    print(f"    name: {skill.name}")
    print(f"    goal: {skill.goal[:80]}")
    print(f"    tools: {skill.tools}")
    print(f"    steps ({len(skill.steps)}):")
    for i, step in enumerate(skill.steps):
        print(f"      {i+1}. {step[:70]}")
    print()

    # Step 4: 模拟 SkillExecutor._execute_step 的 LLM 调用
    from core.llm import chat

    print(f"[4] 模拟第一步执行（SkillExecutor._execute_step）:")
    step = skill.steps[0] if skill.steps else "扫描目标目录"
    available_tools = registry.get_schemas()
    tool_names = registry.get_available_names()

    prompt = f"""你需要执行以下步骤：
步骤 1: {step}

用户原始输入: {user_input}

可用工具: {', '.join(tool_names[:30])}

请调用合适的工具来完成这一步。如果这一步不需要工具调用（如分析、总结），直接输出结果。
如果步骤涉及高风险操作（删除、发送），请先说明操作计划。"""

    messages = [
        {"role": "system", "content": "你是一个技能执行器。按步骤调用工具完成任务，不要做额外的事。"},
        {"role": "user", "content": prompt},
    ]

    print(f"    system prompt: '你是一个技能执行器...'")
    print(f"    tools 数量: {len(available_tools[:20])}")
    print(f"    调用 LLM...")

    result = await chat(messages, tools=available_tools[:20])

    has_tool_calls = "tool_calls" in result
    print(f"\n    结果:")
    print(f"    tool_calls: {has_tool_calls}")
    print(f"    content_len: {len(result.get('content', ''))}")
    if has_tool_calls:
        for tc in result["tool_calls"]:
            print(f"    → {tc['function']['name']}({tc['function']['arguments'][:100]})")
    else:
        print(f"    content: {result.get('content', '')[:500]}")

    # Step 5: 对比 — 用 Conversation 的完整 system prompt
    print(f"\n[5] 对比：用完整 Conversation system prompt:")
    from memory.memory_system import MemorySystem
    from security.context_sanitizer import get_security_prompt

    memory = MemorySystem()
    full_system = memory.get_system_prompt()
    full_system += "\n\n" + get_security_prompt()

    # 把技能信息也注入
    from skills.loader import get_skill_prompt_context
    skill_ctx = get_skill_prompt_context()
    if skill_ctx:
        full_system += "\n\n" + skill_ctx

    messages2 = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": user_input},
    ]

    print(f"    system prompt 长度: {len(full_system)} 字符")
    print(f"    tools 数量: {len(available_tools)}")
    print(f"    调用 LLM...")

    result2 = await chat(messages2, tools=available_tools, use_ollama=False)

    has_tool_calls2 = "tool_calls" in result2
    print(f"\n    结果:")
    print(f"    tool_calls: {has_tool_calls2}")
    print(f"    content_len: {len(result2.get('content', ''))}")
    if has_tool_calls2:
        for tc in result2["tool_calls"]:
            print(f"    → {tc['function']['name']}({tc['function']['arguments'][:100]})")
    else:
        print(f"    content: {result2.get('content', '')[:500]}")


if __name__ == "__main__":
    asyncio.run(main())
