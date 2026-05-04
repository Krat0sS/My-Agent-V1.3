"""
技能执行器 — 按 SKILL.md 的步骤序列调用工具

接收一个已加载的 Skill 对象和用户输入，
按 ## 执行步骤 顺序调用工具，在检查点暂停等待确认。
"""
import json
import time
import asyncio
from typing import Callable, Optional, List
from skills.loader import Skill
from tools.registry import registry
from data import execution_log


class SkillExecutor:
    """技能执行器"""

    def __init__(self, skill: Skill, on_progress: Callable = None,
                 on_confirm: Callable = None, session_id: str = ""):
        """
        Args:
            skill: 要执行的技能
            on_progress: 进度回调 fn(message: str)
            on_confirm: 确认回调 fn(prompt: str) -> bool
            session_id: 会话 ID
        """
        self.skill = skill
        self.on_progress = on_progress or (lambda msg: None)
        self.on_confirm = on_confirm
        self.session_id = session_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def execute(self, user_input: str = "") -> dict:
        """
        执行技能。返回：
        {
            "success": bool,
            "skill": str,
            "steps_completed": int,
            "steps_total": int,
            "results": [...],
            "duration_ms": int,
            "error": str (if failed)
        }
        """
        start_time = time.time()
        steps = self.skill.steps
        results = []

        self.on_progress(f"🎯 开始执行技能「{self.skill.name}」，共 {len(steps)} 步")

        # 检查前置工具是否可用
        missing_tools = []
        for tool_name in self.skill.tools:
            if registry.get(tool_name) is None:
                missing_tools.append(tool_name)
        if missing_tools:
            return {
                "success": False,
                "skill": self.skill.name,
                "steps_completed": 0,
                "steps_total": len(steps),
                "results": [],
                "duration_ms": int((time.time() - start_time) * 1000),
                "error": f"缺少前置工具: {', '.join(missing_tools)}",
            }

        for i, step in enumerate(steps):
            if self._cancelled:
                results.append({"step": i + 1, "status": "cancelled"})
                break

            self.on_progress(f"⏳ 步骤 {i + 1}/{len(steps)}: {step[:60]}...")

            # 执行这一步（通过 LLM 解析步骤 → 工具调用）
            step_result = await self._execute_step(step, user_input, i + 1)
            results.append(step_result)

            if not step_result.get("success"):
                self.on_progress(f"❌ 步骤 {i + 1} 失败: {step_result.get('error', '未知错误')}")
                # 记录失败
                execution_log.log_skill_usage(
                    self.skill.name, user_input,
                    success=False,
                    duration_ms=int((time.time() - start_time) * 1000),
                )
                return {
                    "success": False,
                    "skill": self.skill.name,
                    "steps_completed": i,
                    "steps_total": len(steps),
                    "results": results,
                    "duration_ms": int((time.time() - start_time) * 1000),
                    "error": step_result.get("error", "步骤执行失败"),
                }

            self.on_progress(f"✅ 步骤 {i + 1} 完成")

        duration_ms = int((time.time() - start_time) * 1000)

        # 记录成功
        execution_log.log_skill_usage(
            self.skill.name, user_input,
            success=True, duration_ms=duration_ms,
        )

        self.on_progress(f"🎉 技能「{self.skill.name}」执行完成")

        return {
            "success": True,
            "skill": self.skill.name,
            "steps_completed": len(steps),
            "steps_total": len(steps),
            "results": results,
            "duration_ms": duration_ms,
        }

    async def _execute_step(self, step: str, user_input: str, step_num: int) -> dict:
        """
        执行单个步骤。
        通过 LLM 将步骤描述转换为具体的工具调用。
        """
        from core.llm import chat

        # 构建上下文：告诉 LLM 当前步骤和可用工具
        available_tools = registry.get_schemas()
        tool_names = registry.get_available_names()

        prompt = f"""你需要执行以下步骤：
步骤 {step_num}: {step}

用户原始输入: {user_input}

可用工具: {', '.join(tool_names[:30])}

请调用合适的工具来完成这一步。如果这一步不需要工具调用（如分析、总结），直接输出结果。
如果步骤涉及高风险操作（删除、发送），请先说明操作计划。"""

        messages = [
            {"role": "system", "content": "你是一个技能执行器。按步骤调用工具完成任务，不要做额外的事。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await chat(messages, tools=available_tools[:20])
        except Exception as e:
            return {"success": False, "step": step_num, "error": str(e)}

        if result.get("_error") or result.get("_timeout"):
            return {"success": False, "step": step_num, "error": result.get("content", "LLM 调用失败")}

        # 如果 LLM 调用了工具，执行它
        if "tool_calls" in result:
            tool_results = []
            for tc in result["tool_calls"]:
                func_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                # 高风险工具需要确认
                td = registry.get(func_name)
                if td and td.risk_level == "high" and self.on_confirm:
                    confirmed = self.on_confirm(f"{func_name}({json.dumps(args, ensure_ascii=False)[:100]})")
                    if not confirmed:
                        tool_results.append({"tool": func_name, "cancelled": True})
                        continue

                tool_result = registry.execute(func_name, args)
                tool_results.append({"tool": func_name, "result": tool_result[:500]})

            return {
                "success": True,
                "step": step_num,
                "action": step,
                "tool_calls": tool_results,
                "llm_response": result.get("content", ""),
            }
        else:
            # LLM 没有调用工具，直接返回文本结果
            return {
                "success": True,
                "step": step_num,
                "action": step,
                "tool_calls": [],
                "llm_response": result.get("content", ""),
            }
