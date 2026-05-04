"""
任务规划器 — 把复杂指令分解为可执行步骤
灵感来源：UFO³ DAG 编排、AutoGPT 任务队列
"""
import json
import asyncio
from typing import Optional

import config


PLAN_SYSTEM_PROMPT = """你是一个任务规划专家。把用户指令分解为明确的执行步骤。

输出 JSON 格式：
{
  "goal": "最终目标",
  "steps": [
    {
      "id": 1,
      "action": "步骤描述",
      "tool": "建议使用的工具名",
      "params": {"参数": "值"},
      "verify": "如何验证这步成功了",
      "depends_on": []
    }
  ],
  "estimated_tools": 5,
  "risk": "low|medium|high"
}

规则：
1. 每步只做一件事
2. 涉及 GUI 操作必须先截图确认位置
3. 每步都要有验证方法
4. 依赖关系用 depends_on 表示步骤 ID
5. 高风险操作（删除、发送）放在最后"""


async def plan_task(user_message: str, context: str = "") -> dict:
    """
    用 LLM 分析用户指令，生成执行计划。

    Args:
        user_message: 用户指令
        context: 额外上下文（如当前窗口状态）

    Returns:
        {"goal": "...", "steps": [...], "estimated_tools": N}
    """
    from core.llm import chat

    prompt = f"用户指令：{user_message}"
    if context:
        prompt += f"\n\n当前上下文：{context}"

    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]

    result = await chat(messages, temperature=0.1)

    if result.get("_error") or result.get("_timeout"):
        return {"goal": user_message, "steps": [], "error": result.get("content", "")}

    content = result["content"]

    # 提取 JSON
    try:
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content
        plan = json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        plan = {
            "goal": user_message,
            "steps": [{"id": 1, "action": content, "tool": "auto", "params": {}}],
            "estimated_tools": 1,
            "error": "规划解析失败，使用原始指令"
        }

    return plan


def should_plan(user_message: str) -> bool:
    """
    判断是否需要规划。简单指令直接执行，复杂指令走规划。

    需要规划的特征：
    - 包含多个动作（和、然后、接着）
    - 涉及 GUI 操作（打开、点击、输入、发送）
    - 涉及文件操作（整理、分类、移动）
    """
    multi_action_keywords = ["然后", "接着", "之后", "再", "并且", "同时",
                              "先", "最后", "和", "并"]
    gui_keywords = ["打开", "点击", "输入", "发送", "关闭", "切换",
                     "选择", "拖拽", "滚动", "双击", "找到", "搜索",
                     "查看", "拖动"]
    file_keywords = ["整理", "分类", "移动", "重命名", "删除", "归档",
                      "备份", "清理", "复制"]

    all_keywords = multi_action_keywords + gui_keywords + file_keywords
    keyword_count = sum(1 for kw in all_keywords if kw in user_message)

    # 包含 2+ 个关键词，或者指令长度超过 20 字
    return keyword_count >= 2 or len(user_message) > 20


def format_plan(plan: dict) -> str:
    """把执行计划格式化为可读文本"""
    lines = [f"📋 目标：{plan.get('goal', '未知')}"]
    steps = plan.get("steps", [])
    if steps:
        lines.append(f"📝 {len(steps)} 步计划：")
        for step in steps:
            deps = step.get("depends_on", [])
            dep_str = f" (依赖步骤 {','.join(map(str, deps))})" if deps else ""
            lines.append(f"  {step['id']}. {step['action']}{dep_str}")
            if step.get("verify"):
                lines.append(f"     ✓ 验证：{step['verify']}")
    risk = plan.get("risk", "low")
    if risk != "low":
        lines.append(f"⚠️ 风险等级：{risk}")
    return "\n".join(lines)
