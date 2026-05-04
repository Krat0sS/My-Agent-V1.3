"""对话管理器 — Agent 核心循环（v1.3：修复 decompose_task 重复调用 + 路由日志）"""
import json
import os
import time
import datetime
import asyncio
import atexit
from typing import Callable, Optional
from core.llm import chat
from tools.registry import registry
from memory.memory_system import MemorySystem
from security.context_sanitizer import get_security_prompt
from data import execution_log
import config


class Conversation:
    """一次对话会话（v1.3 async）"""

    def __init__(self, session_id: str = "default", restore: bool = True):
        self.session_id = session_id
        self.memory = MemorySystem()
        self.messages: list[dict] = []
        self.tool_call_count = 0
        self.tool_log: list[dict] = []
        self._browser_session = None
        self._cancel_event = asyncio.Event()
        self._token_usage = []

        if restore and self._session_file_exists():
            self._load_session()
        else:
            self._init_system()

    # ═══ 初始化 ═══

    def _init_system(self):
        system_prompt = self.memory.get_system_prompt()
        # v1.1: 注入安全规则
        system_prompt += "\n\n" + get_security_prompt()
        # v1.1: 注入技能列表
        try:
            from skills.loader import get_skill_prompt_context
            skill_context = get_skill_prompt_context()
            if skill_context:
                system_prompt += "\n\n" + skill_context
        except Exception:
            pass
        self.messages = [{"role": "system", "content": system_prompt}]

    @property
    def browser(self):
        if self._browser_session is None:
            from tools.browser import BrowserSession
            self._browser_session = BrowserSession()
        return self._browser_session

    async def cleanup(self):
        if self._browser_session:
            self._browser_session.close()
            self._browser_session = None

    def cancel(self):
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _clear_cancel(self):
        self._cancel_event.clear()

    # ═══ 自动记忆提取 ═══

    def _extract_memos(self, text: str) -> list[str]:
        import re
        memos = []
        pattern = r'\[MEMO:\s*(.*?)\]'
        for match in re.finditer(pattern, text, re.DOTALL):
            content = match.group(1).strip()
            if content and len(content) > 2:
                memos.append(content)
        return memos

    def _process_memos(self, text: str):
        memos = self._extract_memos(text)
        for memo in memos:
            self.memory.save_daily(f"[自动记忆] {memo}")
            pref_keywords = ["喜欢", "偏好", "习惯", "以后", "不要", "总是", "用中文", "简洁", "详细"]
            if any(kw in memo for kw in pref_keywords):
                self.memory.save_file_preference("auto", memo)
        return len(memos)

    # ═══ 会话持久化 ═══

    def _session_path(self) -> str:
        os.makedirs(config.SESSIONS_DIR, exist_ok=True)
        return os.path.join(config.SESSIONS_DIR, f"{self.session_id}.json")

    def _session_file_exists(self) -> bool:
        return os.path.exists(self._session_path())

    def save_session(self):
        try:
            with open(self._session_path(), "w", encoding="utf-8") as f:
                json.dump({
                    "session_id": self.session_id,
                    "messages": self.messages,
                    "saved_at": datetime.datetime.now().isoformat(),
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_session(self):
        try:
            with open(self._session_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.messages = data.get("messages", [])
            if not self.messages:
                self._init_system()
        except Exception:
            self._init_system()

    # ═══ 上下文保护 ═══

    def _trim_context(self):
        system_msgs = [m for m in self.messages if m["role"] == "system"]
        history = [m for m in self.messages if m["role"] != "system"]
        max_messages = config.MAX_CONTEXT_TURNS * 2
        if len(history) <= max_messages:
            return
        old_history = history[:-max_messages]
        recent_history = history[-max_messages:]
        condensed = []
        for msg in old_history:
            if msg["role"] == "assistant" and "content" in msg:
                content = msg["content"]
                if len(content) > 200:
                    condensed.append({"role": "assistant", "content": f"[历史摘要] ...{content[-200:]}"})
                else:
                    condensed.append(msg)
            elif msg["role"] == "user":
                condensed.append(msg)
        while recent_history and recent_history[0]["role"] in ("tool", "assistant"):
            if recent_history[0]["role"] == "tool":
                recent_history.pop(0)
            elif "tool_calls" in recent_history[0]:
                recent_history.pop(0)
            else:
                break
        self.messages = system_msgs + condensed[-20:] + recent_history

    def _sanitize_messages(self):
        import re
        tool_call_ids_needed = set()
        tool_call_ids_found = set()
        for msg in self.messages:
            if msg["role"] == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    tool_call_ids_needed.add(tc["id"])
            if msg["role"] == "tool":
                tool_call_ids_found.add(msg.get("tool_call_id", ""))
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 100000:
                    try:
                        data = json.loads(content)
                        if "base64" in data:
                            b64_len = len(data["base64"])
                            data["base64"] = f"[图片已省略，{b64_len} 字符 base64]"
                            if "note" in data:
                                del data["note"]
                            msg["content"] = json.dumps(data, ensure_ascii=False)
                    except (json.JSONDecodeError, TypeError):
                        pass
        missing = tool_call_ids_needed - tool_call_ids_found
        if not missing:
            return
        cancel_result = json.dumps({"cancelled": True, "message": "操作未完成（历史修复）。"})
        fixed = []
        for msg in self.messages:
            fixed.append(msg)
            if msg["role"] == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    if tc["id"] in missing:
                        fixed.append({"role": "tool", "tool_call_id": tc["id"], "content": cancel_result})
                        missing.discard(tc["id"])
        self.messages = fixed

    # ═══ 工具执行（v1.1：使用 ToolRegistry） ═══

    async def _execute_tool(self, func_name: str, args: dict,
                            on_confirm: Optional[Callable[[str], bool]] = None) -> str:
        start_time = time.time()
        loop = asyncio.get_running_loop()

        browser_session_tools = {
            "browser_click", "browser_type", "browser_press_key",
            "browser_download", "browser_session_screenshot", "browser_get_content",
            "browser_wait",
        }
        subprocess_tools = {"run_command", "run_command_confirmed"}

        if func_name in browser_session_tools:
            try:
                result_raw = await self._execute_browser_session_tool(func_name, args)
            except asyncio.CancelledError:
                result_raw = json.dumps({"cancelled": True, "message": "操作已被用户取消。"})
            except Exception as e:
                result_raw = json.dumps({"error": True, "message": f"浏览器工具失败: {str(e)}"})
            elapsed = time.time() - start_time
            self._log_tool_call(func_name, args, result_raw, elapsed, 0)
            execution_log.log_tool_call(
                func_name, args, result_raw[:500],
                success="error" not in result_raw.lower(),
                elapsed_ms=int(elapsed * 1000),
                session_id=self.session_id,
            )
            return result_raw

        if func_name in subprocess_tools:
            from tools.subprocess_runner import run_command_async, run_command_confirmed_async
            try:
                if func_name == "run_command":
                    result_raw = await run_command_async(args.get("command", ""), args.get("cwd"), args.get("timeout", 30))
                else:
                    result_raw = await run_command_confirmed_async(args.get("command", ""), args.get("cwd"), args.get("timeout", 30))
            except asyncio.CancelledError:
                result_raw = json.dumps({"cancelled": True, "message": "命令已被用户取消。"})
            elapsed = time.time() - start_time
            self._log_tool_call(func_name, args, result_raw, elapsed, 0)
            execution_log.log_tool_call(func_name, args, result_raw[:500], success="error" not in result_raw.lower(), elapsed_ms=int(elapsed * 1000), session_id=self.session_id)
            return result_raw

        # v1.1: 使用 ToolRegistry 执行
        try:
            result_raw = await asyncio.wait_for(
                loop.run_in_executor(None, registry.execute, func_name, args),
                timeout=config.TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            error_result = json.dumps({"error": True, "type": "tool_timeout", "tool": func_name, "message": f"工具 {func_name} 执行超时 ({config.TOOL_TIMEOUT}s)"}, ensure_ascii=False)
            self._log_tool_call(func_name, args, error_result, elapsed, 0, error=True)
            execution_log.log_tool_call(func_name, args, error_result[:500], success=False, elapsed_ms=int(elapsed * 1000), session_id=self.session_id)
            return error_result
        except asyncio.CancelledError:
            elapsed = time.time() - start_time
            cancel_result = json.dumps({"cancelled": True, "message": "操作已被用户取消。"}, ensure_ascii=False)
            self._log_tool_call(func_name, args, cancel_result, elapsed, 0, error=False)
            return cancel_result
        except Exception as e:
            elapsed = time.time() - start_time
            error_result = json.dumps({"error": True, "type": "execution_error", "tool": func_name, "message": f"工具 {func_name} 执行失败: {str(e)}"}, ensure_ascii=False)
            self._log_tool_call(func_name, args, error_result, elapsed, 0, error=True)
            execution_log.log_tool_call(func_name, args, error_result[:500], success=False, elapsed_ms=int(elapsed * 1000), session_id=self.session_id)
            return error_result

        # 确认检查
        try:
            parsed = json.loads(result_raw)
            if isinstance(parsed, dict) and parsed.get("needs_confirm"):
                cmd = parsed.get("command", "")
                if on_confirm:
                    confirmed = on_confirm(cmd)
                    if confirmed:
                        result_raw = await asyncio.wait_for(loop.run_in_executor(None, registry.execute, "run_command_confirmed", {"command": cmd}), timeout=config.TOOL_TIMEOUT)
                    else:
                        result_raw = json.dumps({"cancelled": True, "message": "用户取消了该命令的执行。"}, ensure_ascii=False)
                else:
                    result_raw = json.dumps({"error": True, "type": "confirm_required", "message": f"该命令需要用户确认: {cmd}"}, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass

        elapsed = time.time() - start_time
        self._log_tool_call(func_name, args, result_raw, elapsed, 0)
        execution_log.log_tool_call(func_name, args, result_raw[:500], success="error" not in result_raw.lower(), elapsed_ms=int(elapsed * 1000), session_id=self.session_id)
        return result_raw

    async def _execute_browser_session_tool(self, func_name: str, args: dict) -> str:
        if func_name == "browser_click":
            return await self.browser.click(args.get("selector", ""))
        elif func_name == "browser_type":
            return await self.browser.type_text(args.get("selector", ""), args.get("text", ""), args.get("press_enter", False))
        elif func_name == "browser_press_key":
            return await self.browser.press_key(args.get("key", ""))
        elif func_name == "browser_download":
            return await self.browser.download(args.get("url", ""), args.get("save_dir"))
        elif func_name == "browser_session_screenshot":
            return await self.browser.screenshot(args.get("full_page", True))
        elif func_name == "browser_get_content":
            return await self.browser.get_content()
        elif func_name == "browser_wait":
            return await self.browser.wait_for_selector(args.get("selector", ""), args.get("timeout", 10000))
        else:
            return json.dumps({"error": f"未知浏览器工具: {func_name}"})

    def _log_tool_call(self, func_name: str, args: dict, result: str, elapsed: float, retries: int, error: bool = False):
        entry = {
            "tool": func_name,
            "args": {k: str(v)[:100] for k, v in args.items()},
            "elapsed_ms": int(elapsed * 1000),
            "retries": retries,
            "error": error,
            "result_preview": result[:200] if not error else result,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        self.tool_log.append(entry)

    # ═══ 对话主循环（v1.3：修复 decompose_task 重复调用 + 路由日志） ═══

    async def send(self, user_message: str,
                   on_confirm: Optional[Callable[[str], bool]] = None,
                   on_progress: Optional[Callable[[str], None]] = None) -> dict:
        """
        v1.3 异步发送用户消息，获取助手回复。

        修复：
        - decompose_task 只调用一次，计划结果缓存复用
        - simple 指令也记录路由决策日志
        - 技能生成阈值从 tool_call_count >= 2 提升到 >= 3
        """
        self.messages.append({"role": "user", "content": user_message})
        self.tool_call_count = 0
        self._clear_cancel()
        self._token_usage = []
        rounds = 0
        start_time = time.time()

        self._trim_context()
        self._sanitize_messages()

        # ═══ v1.1: 意图路由 ═══
        from core.intent_router import route, decompose_task, generate_skill_md, save_skill
        from skills.loader import load_all_skills
        from skills.executor import SkillExecutor

        skills = load_all_skills()
        routing = await route(user_message, skills)

        # v1.3: 缓存 decompose 结果，避免重复调用
        cached_plan = None

        # —— 简单指令：直接走 LLM 对话 ——
        if routing.action == "direct_tool":
            # v1.3: simple 指令也记录路由决策
            execution_log.log_routing_decision(
                user_message,
                candidates=[{"skill": name, "score": round(s, 3)} for name, s in routing.candidates] if routing.candidates else [],
                fallback_to_decompose=False,
            )

        # —— 技能匹配命中：用 SkillExecutor 极速执行 ——
        elif routing.action == "execute_skill" and routing.matched_skill:
            if on_progress:
                on_progress(f"🎯 命中技能「{routing.matched_skill.name}」(置信度 {routing.match_score:.2f})")

            executor = SkillExecutor(
                routing.matched_skill,
                on_progress=on_progress,
                on_confirm=on_confirm,
                session_id=self.session_id,
            )

            skill_result = await executor.execute(user_message)

            # 记录任务执行
            duration_ms = int((time.time() - start_time) * 1000)
            execution_log.log_task(
                user_input=user_message,
                matched_skill=routing.matched_skill.name,
                match_score=routing.match_score,
                success=skill_result.get("success", False),
                duration_ms=duration_ms,
                session_id=self.session_id,
            )

            # v1.3: 记录路由决策
            execution_log.log_routing_decision(
                user_message,
                candidates=[{"skill": name, "score": round(s, 3)} for name, s in routing.candidates] if routing.candidates else [],
                chosen_skill=routing.matched_skill.name,
                chosen_score=routing.match_score,
                fallback_to_decompose=False,
            )

            if skill_result.get("success"):
                response = f"✅ 已通过技能「{routing.matched_skill.name}」完成任务"
                # 汇总结果
                results = skill_result.get("results", [])
                for r in results:
                    if r.get("llm_response"):
                        response += f"\n{r['llm_response']}"

                self.messages.append({"role": "assistant", "content": response})
                self.save_session()
                return self._build_result(response, 1)
            else:
                # 技能执行失败，回退到普通对话
                if on_progress:
                    on_progress(f"⚠️ 技能执行失败，回退到普通对话: {skill_result.get('error', '')}")

        # —— 复杂任务或未命中：分解 → 执行 → 沉淀 ——
        elif routing.action == "decompose":
            if on_progress:
                on_progress("📝 未命中已有技能，正在分解任务...")

            # v1.3: 只调用一次 decompose_task，缓存结果
            cached_plan = await decompose_task(user_message)

            if cached_plan.get("steps") and not cached_plan.get("error"):
                # 把计划注入系统提示
                plan_text = f"📋 目标：{cached_plan.get('goal', user_message)}\n"
                for step in cached_plan["steps"]:
                    deps = step.get("depends_on", [])
                    dep_str = f" (依赖步骤 {','.join(map(str, deps))})" if deps else ""
                    plan_text += f"  {step['id']}. {step['action']}{dep_str}\n"

                self.messages.append({
                    "role": "system",
                    "content": f"[任务规划]\n{plan_text}\n\n请按以上步骤逐步执行。"
                })

            # v1.3: 记录路由决策
            execution_log.log_routing_decision(
                user_message,
                candidates=[{"skill": name, "score": round(s, 3)} for name, s in routing.candidates] if routing.candidates else [],
                fallback_to_decompose=True,
            )

        # ═══ 普通 LLM 对话循环 ═══
        while self.tool_call_count < config.MAX_TOOL_CALLS_PER_TURN:
            if self.is_cancelled():
                fallback = "操作已被用户取消。"
                self.messages.append({"role": "assistant", "content": fallback})
                return self._build_result(fallback, rounds)

            response = await chat(self.messages, tools=registry.get_schemas())
            rounds += 1

            if "_usage" in response:
                self._token_usage.append(response["_usage"])

            if response.get("_timeout") or response.get("_error"):
                assistant_msg = response["content"]
                self.messages.append({"role": "assistant", "content": assistant_msg})
                return self._build_result(assistant_msg, rounds)

            if "tool_calls" not in response:
                assistant_msg = response["content"]
                self.messages.append({"role": "assistant", "content": assistant_msg})
                memo_count = self._process_memos(assistant_msg)
                tool_summary = ""
                if self.tool_log:
                    recent_tools = self.tool_log[-5:]
                    tool_names = [t["tool"] for t in recent_tools]
                    tool_summary = f"\n工具调用: {', '.join(tool_names)}"
                memo_summary = f"\n自动记忆: {memo_count} 条" if memo_count > 0 else ""
                self.memory.save_daily(
                    f"用户: {user_message[:200]}\n"
                    f"助手: {assistant_msg[:200]}{tool_summary}{memo_summary}"
                )

                # v1.3: 分解模式下，任务成功且步骤 >= 3 时才生成新技能
                # 复用 cached_plan，不再重复调用 decompose_task
                if routing.action == "decompose" and self.tool_call_count >= 3:
                    try:
                        if cached_plan and cached_plan.get("skill_name"):
                            skill_md = await generate_skill_md(user_message, cached_plan, [])
                            if skill_md:
                                skill_path = save_skill(cached_plan["skill_name"], skill_md)
                                if on_progress:
                                    on_progress(f"💡 新技能已生成: {cached_plan['skill_name']}")
                    except Exception:
                        pass  # 技能生成失败不影响正常回复

                # 记录任务执行
                duration_ms = int((time.time() - start_time) * 1000)
                execution_log.log_task(
                    user_input=user_message,
                    matched_skill=routing.matched_skill.name if routing.matched_skill else None,
                    match_score=routing.match_score,
                    success=True,
                    duration_ms=duration_ms,
                    session_id=self.session_id,
                )

                self.save_session()
                return self._build_result(assistant_msg, rounds)

            self.messages.append(response)
            self.tool_call_count += 1

            if response.get("content"):
                self._process_memos(response["content"])

            for tc in response["tool_calls"]:
                if self.is_cancelled():
                    cancel_result = json.dumps({"cancelled": True, "message": "操作已被用户取消。"})
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": cancel_result})
                    continue

                func_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                result = await self._execute_tool(func_name, args, on_confirm=on_confirm)

                # Vision 自动分析截图
                if func_name in ("desktop_screenshot", "browser_session_screenshot") and "base64" in result:
                    try:
                        result_data = json.loads(result)
                        if result_data.get("base64") and not result_data.get("error"):
                            from tools.vision import analyze_screenshot_sync
                            vision = analyze_screenshot_sync(result_data["base64"])
                            if not vision.get("error"):
                                result_data["vision_analysis"] = vision
                                result = json.dumps(result_data, ensure_ascii=False)
                    except (json.JSONDecodeError, Exception):
                        pass

                self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

                # GUI 操作后自动验证
                GUI_VERIFY_TOOLS = {"desktop_click", "desktop_double_click", "desktop_type", "desktop_keys", "browser_click", "browser_type"}
                if func_name in GUI_VERIFY_TOOLS:
                    try:
                        tool_result = json.loads(result)
                        if tool_result.get("success"):
                            verify_result = await self._execute_tool("desktop_screenshot", {}, on_confirm=on_confirm)
                            try:
                                verify_data = json.loads(verify_result)
                                if verify_data.get("base64") and not verify_data.get("error"):
                                    from tools.vision import analyze_screenshot_sync
                                    vision = analyze_screenshot_sync(verify_data["base64"], f"刚才执行了 {func_name} 操作，请判断操作是否成功。简短回答。")
                                    if not vision.get("error"):
                                        verify_data["verification"] = vision.get("description", "操作已执行")
                                        verify_result = json.dumps(verify_data, ensure_ascii=False)
                            except Exception:
                                pass
                            self.messages.append({"role": "system", "content": f"[操作验证] {func_name} 执行后截图: {verify_result[:500]}"})
                    except (json.JSONDecodeError, Exception):
                        pass

        fallback = "我执行了太多工具调用，请简化你的请求。"
        self.messages.append({"role": "assistant", "content": fallback})
        self.save_session()
        return self._build_result(fallback, rounds)

    def _build_result(self, response: str, rounds: int) -> dict:
        total_prompt = sum(u.get("prompt_tokens", 0) for u in self._token_usage)
        total_completion = sum(u.get("completion_tokens", 0) for u in self._token_usage)
        total_tokens = sum(u.get("total_tokens", 0) for u in self._token_usage)
        estimated_cost = (total_prompt * 0.5 + total_completion * 2.0) / 1_000_000
        return {
            "response": response,
            "tool_calls": self.tool_log[-10:],
            "stats": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "tool_calls_count": self.tool_call_count,
                "rounds": rounds,
                "estimated_cost_cny": round(estimated_cost, 4),
            }
        }

    # ═══ 工具 ═══

    def get_history(self) -> list[dict]:
        return [m for m in self.messages if m["role"] != "system"]

    def get_tool_log(self) -> list[dict]:
        return self.tool_log

    def reset(self):
        if self._browser_session:
            self._browser_session.close()
            self._browser_session = None
        self.messages = []
        self.tool_log = []
        self._token_usage = []
        self._init_system()
        self.save_session()


class ConversationManager:
    """多会话管理"""

    def __init__(self):
        self.sessions: dict[str, Conversation] = {}
        atexit.register(self._cleanup_all)

    def _cleanup_all(self):
        for conv in self.sessions.values():
            if conv._browser_session:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(conv.cleanup())
                    else:
                        loop.run_until_complete(conv.cleanup())
                except Exception:
                    pass

    def get_or_create(self, session_id: str = "default") -> Conversation:
        if session_id not in self.sessions:
            self.sessions[session_id] = Conversation(session_id)
        return self.sessions[session_id]

    def list_sessions(self) -> list[str]:
        return list(self.sessions.keys())

    def delete_session(self, session_id: str):
        conv = self.sessions.pop(session_id, None)
        if conv:
            try:
                os.remove(conv._session_path())
            except OSError:
                pass
