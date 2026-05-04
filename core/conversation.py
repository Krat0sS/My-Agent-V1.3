# -*- coding: utf-8 -*-
"""对话管理器 — Agent 核心循环（v1.3.5：五层易经流水线）"""
import json
import os
import re
import time
import datetime
import asyncio
import atexit
from dataclasses import dataclass
from typing import Callable, Optional, List
from core.llm import chat
from tools.registry import registry
from memory.memory_system import MemorySystem
from security.context_sanitizer import get_security_prompt
from data import execution_log
import config

# v1.3.5: 易经五层流水线（taiji 诊断硬嵌入 Conversation，不从外部 import）
from core.change_engine import assess_yao, execute_recovery, format_yao_message
from core.wanwu import wanwu_generate, record_wanwu_result, check_promotion_candidates, promote_to_skill
from core.orchestrator import orchestrate, log_orchestration, format_orchestration_message
from core.temporal import get_temporal_context, record_task_pattern, handle_suggestion_rejection, format_temporal_message


# ═══════════════════════════════════════════════════════════
# 太极诊断常量（硬嵌入，不依赖外部模块）
# ═══════════════════════════════════════════════════════════

_HEXAGRAM_ACTION = {
    ('old_yang', 'old_yang'):    ('乾为天',      'full_execute'),
    ('old_yang', 'young_yang'):  ('天风姤',      'execute_with_watch'),
    ('old_yang', 'young_yin'):   ('天山遁',      'execute_partial'),
    ('old_yang', 'old_yin'):     ('天地否',      'pause_ask'),
    ('young_yang', 'old_yang'):  ('风天小畜',    'execute_normal'),
    ('young_yang', 'young_yang'):('巽为风',      'execute_cautious'),
    ('young_yang', 'young_yin'): ('风山渐',      'step_by_step'),
    ('young_yang', 'old_yin'):   ('风地观',      'observe_first'),
    ('young_yin', 'old_yang'):   ('山天大畜',    'retry_then_execute'),
    ('young_yin', 'young_yang'): ('山风蛊',      'fix_then_continue'),
    ('young_yin', 'young_yin'):  ('艮为山',      'stop_analyze'),
    ('young_yin', 'old_yin'):    ('山地剥',      'rollback_request'),
    ('old_yin', 'old_yang'):     ('地天泰',      'recover_easy'),
    ('old_yin', 'young_yang'):   ('地风升',      'recover_step'),
    ('old_yin', 'young_yin'):    ('地山谦',      'humble_rollback'),
    ('old_yin', 'old_yin'):      ('坤为地',      'full_stop'),
}

_VAGUE_PATTERNS = ['帮我', '看看', '弄一下', '搞一下', '处理', '解决', '怎么办', '什么', '为什么', '怎么样']


@dataclass
class _TaijiResult:
    """太极诊断结果（Conversation 内部使用）"""
    inner: str
    outer: str
    inner_score: float
    outer_score: float
    hexagram: str
    action_hint: str


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

    # ═══ 上下文保护（v1.3.4: 智能压缩） ═══

    # 压缩阈值：超过这个轮次就开始压缩旧消息
    _COMPRESS_AFTER_TURNS = 8    # 8 轮后压缩（16 条消息）
    # 保留最近 N 轮完整上下文
    _KEEP_RECENT_TURNS = 5
    # 每条旧消息最大保留字符数
    _MAX_OLD_MSG_LEN = 300

    def _trim_context(self):
        """
        v1.3.4 智能上下文压缩。

        策略：
        - 最近 5 轮（10 条消息）保持完整
        - 更早的消息：保留关键信息，压缩冗余
        - 用户消息：原样保留（是意图记录）
        - 工具结果：保留摘要（工具名 + 结果前 200 字）
        - 助手消息：保留工具调用记录 + 内容摘要
        - 系统消息：永远保留
        """
        system_msgs = [m for m in self.messages if m["role"] == "system"]
        history = [m for m in self.messages if m["role"] != "system"]

        keep_count = self._KEEP_RECENT_TURNS * 2  # 每轮 = user + assistant
        if len(history) <= keep_count:
            return  # 还没到压缩阈值

        old_history = history[:-keep_count]
        recent_history = history[-keep_count:]

        # 压缩旧消息
        condensed = []
        for msg in old_history:
            role = msg.get("role", "")

            if role == "user":
                # 用户消息：原样保留（是意图记录）
                condensed.append(msg)

            elif role == "assistant":
                if "tool_calls" in msg:
                    # 有工具调用的助手消息：保留工具调用名称和参数摘要
                    tool_summary = []
                    for tc in msg.get("tool_calls", []):
                        fn = tc.get("function", {})
                        name = fn.get("name", "?")
                        args_str = fn.get("arguments", "{}")[:80]
                        tool_summary.append(f"{name}({args_str})")
                    condensed.append({
                        "role": "assistant",
                        "content": f"[调用了: {', '.join(tool_summary)}]"
                    })
                elif msg.get("content"):
                    # 纯文本回复：截取关键部分
                    content = msg["content"]
                    if len(content) > self._MAX_OLD_MSG_LEN:
                        # 保留前 150 字 + 后 100 字（开头是结论，结尾可能是总结）
                        condensed.append({
                            "role": "assistant",
                            "content": content[:150] + f"\n...[压缩]...\n" + content[-100:]
                        })
                    else:
                        condensed.append(msg)

            elif role == "tool":
                # 工具结果：保留工具名和结果摘要
                tool_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")
                if len(content) > self._MAX_OLD_MSG_LEN:
                    condensed.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": content[:self._MAX_OLD_MSG_LEN] + "...[已压缩]"
                    })
                else:
                    condensed.append(msg)

        # 确保 recent_history 不以孤立的 tool 消息开头
        while recent_history and recent_history[0]["role"] in ("tool", "assistant"):
            if recent_history[0]["role"] == "tool":
                recent_history.pop(0)
            elif "tool_calls" in recent_history[0]:
                recent_history.pop(0)
            else:
                break

        self.messages = system_msgs + condensed + recent_history

    def get_context_stats(self) -> dict:
        """获取上下文统计（调试用）"""
        system_msgs = [m for m in self.messages if m["role"] == "system"]
        history = [m for m in self.messages if m["role"] != "system"]
        total_chars = sum(len(m.get("content", "")) for m in self.messages)
        return {
            "total_messages": len(self.messages),
            "system_messages": len(system_msgs),
            "history_messages": len(history),
            "total_chars": total_chars,
            "estimated_tokens": total_chars // 2,  # 粗估：中文约 2 字符 = 1 token
        }

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
        success = "error" not in result_raw.lower()
        self._log_tool_call(func_name, args, result_raw, elapsed, 0)
        execution_log.log_tool_call(func_name, args, result_raw[:500], success=success, elapsed_ms=int(elapsed * 1000), session_id=self.session_id)

        # ═══ v1.3.5: 变爻判定（第五层） ═══
        if not success:
            yao = assess_yao(func_name, args, result_raw, success=False,
                             error_message=result_raw[:200])
            recovery = execute_recovery(yao)

            if yao.should_retry and yao.retry_params:
                # 少阴：自动重试一次（调参数）
                try:
                    retry_result = await asyncio.wait_for(
                        loop.run_in_executor(None, registry.execute, func_name, yao.retry_params),
                        timeout=config.TOOL_TIMEOUT
                    )
                    retry_success = "error" not in retry_result.lower()
                    execution_log.log_tool_call(
                        func_name, yao.retry_params, retry_result[:500],
                        success=retry_success,
                        elapsed_ms=int((time.time() - start_time) * 1000),
                        session_id=self.session_id,
                        yao_type='young_yin_retry',
                        recovery_action='retry'
                    )
                    if retry_success:
                        return retry_result
                except Exception:
                    pass  # 重试失败，返回原始错误

            elif yao.should_rollback:
                # 老阴：标记技能不可用
                result_raw = json.dumps({
                    "error": True,
                    "type": "yao_old_yin",
                    "tool": func_name,
                    "message": format_yao_message(yao),
                    "recovery": recovery
                }, ensure_ascii=False)

            elif yao.should_deposit:
                # 老阳：标记为高价值路径
                result_raw = json.dumps({
                    "success": True,
                    "type": "yao_old_yang",
                    "tool": func_name,
                    "result": result_raw[:500],
                    "message": format_yao_message(yao),
                    "deposited": True
                }, ensure_ascii=False)

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

    # ═══════════════════════════════════════════════════════════
    # 太极诊断（硬嵌入 Conversation，不可跳过）
    # ═══════════════════════════════════════════════════════════

    def _taiji_diagnose(self, user_input: str) -> _TaijiResult:
        """
        太极诊断 — send() 的第一行，不可跳过。

        内卦：最近 10 轮工具调用的指数衰减加权评分
        外卦：指令明确度 + 工具可用性
        卦象：16 种组合，字典查表
        """
        start = time.time()

        # ── 内卦：Agent 自身状态 ──
        recent = execution_log.get_recent_tool_calls(limit=10)
        inner_score = self._calculate_inner_score(recent)
        if inner_score > 0.6:
            inner = 'old_yang'
        elif inner_score > 0.2:
            inner = 'young_yang'
        elif inner_score > -0.2:
            inner = 'young_yin'
        else:
            inner = 'old_yin'

        # ── 外卦：任务环境状态 ──
        clarity = self._assess_clarity(user_input)
        tool_ready = self._check_tool_availability(user_input)
        outer_score = (clarity + tool_ready) / 2
        if outer_score > 0.7:
            outer = 'old_yang'
        elif outer_score > 0.45:
            outer = 'young_yang'
        elif outer_score > 0.25:
            outer = 'young_yin'
        else:
            outer = 'old_yin'

        # ── 卦象查表 ──
        hexagram, action_hint = _HEXAGRAM_ACTION.get((inner, outer), ('未知卦', 'full_stop'))

        elapsed_ms = int((time.time() - start) * 1000)

        # ── 写入诊断日志 ──
        execution_log.log_diagnosis(
            inner_state=inner, outer_state=outer,
            inner_score=round(inner_score, 3), outer_score=round(outer_score, 3),
            hexagram=hexagram, action_hint=action_hint,
            elapsed_ms=elapsed_ms, session_id=self.session_id
        )

        return _TaijiResult(
            inner=inner, outer=outer,
            inner_score=round(inner_score, 3), outer_score=round(outer_score, 3),
            hexagram=hexagram, action_hint=action_hint
        )

    @staticmethod
    def _calculate_inner_score(recent_calls: List[dict]) -> float:
        """内卦评分：指数衰减加权（最近成败权重最大，每轮衰减 30%）"""
        if not recent_calls:
            return 0.0
        weight = 1.0
        total = 0.0
        max_possible = 0.0
        for call in reversed(recent_calls[:10]):
            total += (1 if call.get('success', 1) else -1) * weight
            max_possible += weight
            weight *= 0.7
        return total / max_possible if max_possible > 0 else 0.0

    @staticmethod
    def _assess_clarity(user_input: str) -> float:
        """外卦-指令明确度：从 ToolRegistry 动态生成动词表"""
        if not user_input or len(user_input.strip()) < 2:
            return 0.1
        text = user_input.strip()
        score = 0.5
        if len(text) > 10:
            score += 0.1
        # 动态动词表：从工具描述中提取
        verbs = set()
        try:
            for tool in registry.get_all():
                desc = tool.description or ""
                match = re.match(r'^([\u4e00-\u9fff]{2,4})', desc.strip())
                if match:
                    verbs.add(match.group(1))
        except Exception:
            pass
        if len(verbs) < 10:
            verbs.update(['打开', '搜索', '整理', '创建', '删除', '发送', '分析', '查找',
                          '备份', '清理', '关闭', '下载', '上传', '复制', '移动', '监控'])
        has_verb = any(v in text for v in verbs)
        if has_verb:
            score += 0.2
        is_vague = any(p in text for p in _VAGUE_PATTERNS)
        if is_vague and not has_verb:
            score -= 0.3
        words = text.split()
        if len(words) >= 3 or (len(text) > 8 and has_verb):
            score += 0.1
        return max(0.0, min(1.0, score))

    @staticmethod
    def _check_tool_availability(user_input: str) -> float:
        """外卦-工具可用性：检查请求是否能被现有工具覆盖"""
        tool_keywords = {
            'file': ['文件', '搜索', '查找', '整理', '备份', '清理', '移动', '复制'],
            'web': ['网页', '浏览器', '搜索', '下载', '网络'],
            'desktop': ['桌面', '截图', '屏幕', '窗口'],
            'system': ['系统', '进程', 'CPU', '内存', '磁盘'],
        }
        text = user_input if user_input else ''
        matched = sum(1 for kws in tool_keywords.values() if any(k in text for k in kws))
        if matched == 0:
            return 0.5
        elif matched == 1:
            return 0.8
        else:
            return 0.6

    # ═══ 对话主循环 ═══

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

        # ═══ v1.3.5: 太极诊断（第一层，硬嵌入，不可跳过） ═══
        diagnosis = self._taiji_diagnose(user_message)
        if on_progress:
            on_progress(f"☯️ {diagnosis.hexagram}（内={diagnosis.inner} 外={diagnosis.outer}）")

        # 危机状态：内卦为老阴时，直接进入恢复流程
        if diagnosis.inner == 'old_yin':
            if on_progress:
                on_progress(f"⚠️ 系统处于危机状态（{diagnosis.hexagram}），启动恢复流程")
            # 不阻断，让后续 change_engine 处理具体恢复动作

        # ═══ v1.3.5: 时辰感知（第二层） ═══
        temporal_ctx = get_temporal_context()
        if temporal_ctx.suggestion and on_progress:
            on_progress(f"🕐 {format_temporal_message(temporal_ctx)}")

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
                time_slot=temporal_ctx.time_slot,
                task_type=temporal_ctx.energy_level,
            )

            # ═══ v1.3.5: 时辰规律记录 ═══
            if skill_result.get("success"):
                record_task_pattern(user_message, routing.matched_skill.name)

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

        # —— 复杂任务或未命中：先万物生成，再分解 → 执行 → 沉淀 ——
        elif routing.action == "decompose":
            if on_progress:
                on_progress("📝 未命中已有技能，尝试万物组合...")

            # ═══ v1.3.5: 万物生成器（第三层） ═══
            # 在 LLM 分解之前，先尝试将已有技能"相错"组合
            available_skills = [s.name for s in skills] if skills else []
            wanwu_plan = wanwu_generate(user_message, available_skills, session_id=self.session_id)

            if wanwu_plan:
                if on_progress:
                    on_progress(f"🌱 {wanwu_plan.summary()}")

                # 万物计划注入上下文，让 LLM 按计划执行
                plan_text = f"📋 万物组合计划：{wanwu_plan.skill_a}（发起）→ {wanwu_plan.skill_b}（承载）\n"
                for step in wanwu_plan.steps:
                    plan_text += f"  步骤 {step['step']}: {step['action']}\n"

                self.messages.append({
                    "role": "system",
                    "content": f"[万物组合]\n{plan_text}\n\n请按以上步骤执行。这是一个临时组合计划。"
                })

                # 记录路由决策
                execution_log.log_routing_decision(
                    user_message,
                    candidates=[wanwu_plan.skill_a, wanwu_plan.skill_b],
                    chosen_skill=f"{wanwu_plan.skill_a}+{wanwu_plan.skill_b}",
                    chosen_score=1.0,
                    fallback_to_decompose=False,
                )
            else:
                # 万物组合失败，回退到 LLM 分解
                if on_progress:
                    on_progress("📝 万物组合无法匹配，回退到任务分解...")

                # v1.3: 只调用一次 decompose_task，缓存结果
                cached_plan = await decompose_task(user_message)

                if cached_plan.get("steps") and not cached_plan.get("error"):
                    # ═══ v1.3.5: 五行编排（第四层） ═══
                    # 用编排器重排计划步骤
                    step_skills = [s.get('tool', s.get('action', '')) for s in cached_plan["steps"]]
                    if len(step_skills) > 1:
                        orch_result = orchestrate(step_skills)
                        log_orchestration(user_message, orch_result, session_id=self.session_id)
                        if on_progress and orch_result.generate_bonus > 0:
                            on_progress(f"🔄 {format_orchestration_message(orch_result)}")

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

        # ═══ v1.3: 根据路由结果选择 LLM 客户端 ═══
        # simple → Ollama 本地（低成本）
        # medium/complex → DeepSeek 云端（高能力）
        # execute_skill → 技能内部决定，这里不走 LLM
        use_ollama = (routing.action == "direct_tool")

        # ═══ 普通 LLM 对话循环 ═══
        while self.tool_call_count < config.MAX_TOOL_CALLS_PER_TURN:
            if self.is_cancelled():
                fallback = "操作已被用户取消。"
                self.messages.append({"role": "assistant", "content": fallback})
                return self._build_result(fallback, rounds)

            response = await chat(self.messages, tools=registry.get_schemas(),
                                  use_ollama=use_ollama)
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
                    time_slot=temporal_ctx.time_slot,
                    task_type=temporal_ctx.energy_level,
                )

                # ═══ v1.3.5: 时辰规律记录 ═══
                record_task_pattern(user_message, routing.matched_skill.name if routing.matched_skill else None)

                # ═══ v1.3.5: 万物沉淀检查 ═══
                try:
                    candidates = check_promotion_candidates()
                    for cand in candidates:
                        promote_to_skill(cand)
                        if on_progress:
                            on_progress(f"🌱 新技能已沉淀: {cand.skill_a}+{cand.skill_b}（成功 {cand.success_count} 次）")
                except Exception:
                    pass  # 沉淀检查失败不影响正常回复

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

            # v1.3.4: 每轮工具调用后检查是否需要压缩上下文
            self._trim_context()

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
