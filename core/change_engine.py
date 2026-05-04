# -*- coding: utf-8 -*-
"""
变爻状态转换器 — 工具失败后的智能恢复引擎

根据失败模式选择恢复路径：
- 老阴（old_yin）：同参数连续失败≥2次 → 回滚，标记技能不可用，请求用户
- 少阴（young_yin）：偶发失败 → 换工具或调参数重试一次
- 老阳（old_yang）：返回超预期结果 → 沉淀为高价值路径
- 少阳（young_yang）：正常执行 → 不作变动，继续

设计原则：
- 零 API 调用，纯规则
- 结果写入 tool_calls 的 yao_type + recovery_action 列
- 与 taiji_diagnosis 配合：诊断为 old_yin 时直接触发 change_engine
"""
import time
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from data import execution_log as log
from core.taiji import calculate_inner_score


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class YaoResult:
    """变爻判定结果"""
    yao_type: str              # 'old_yin' | 'young_yin' | 'old_yang' | 'young_yang'
    recovery_action: str       # 'rollback' | 'retry' | '沉淀' | 'continue'
    should_retry: bool         # 是否需要重试
    should_rollback: bool      # 是否需要回滚
    should_ask_user: bool      # 是否需要请求用户
    should_deposit: bool       # 是否需要沉淀
    message: str               # 给用户的说明
    retry_params: Optional[Dict[str, Any]] = None  # 重试时的调整参数

    def __str__(self):
        return f"[{self.yao_type}] {self.recovery_action}: {self.message}"


# ═══════════════════════════════════════════════════════════
# 变爻判定
# ═══════════════════════════════════════════════════════════

def assess_yao(tool_name: str, args: dict, result: str,
               success: bool, error_message: str = "",
               recent_calls: List[dict] = None) -> YaoResult:
    """
    变爻判定主入口。

    根据当前工具调用的结果 + 最近同一工具的历史记录，
    判定变爻类型并返回恢复建议。

    Args:
        tool_name: 当前调用的工具名
        args: 当前调用的参数
        result: 工具返回结果
        success: 是否成功
        error_message: 错误信息（如果有）
        recent_calls: 最近的同一工具调用记录（可选，不传则自动查询）

    Returns:
        YaoResult 对象
    """
    # 获取最近同一工具的调用记录
    if recent_calls is None:
        recent_calls = log.get_recent_tool_calls(tool_name=tool_name, limit=5)

    # ═══ 老阳判定：返回了超预期结果 ═══
    if success and _is_surplus_result(tool_name, args, result):
        return YaoResult(
            yao_type='old_yang',
            recovery_action='沉淀',
            should_retry=False,
            should_rollback=False,
            should_ask_user=False,
            should_deposit=True,
            message=f'工具 {tool_name} 返回了超出请求范围的附加信息，已标记为高价值路径。'
        )

    # ═══ 少阳判定：正常执行 ═══
    if success:
        return YaoResult(
            yao_type='young_yang',
            recovery_action='continue',
            should_retry=False,
            should_rollback=False,
            should_ask_user=False,
            should_deposit=False,
            message='执行正常，继续下一步。'
        )

    # ═══ 从这里开始，success=False ═══

    # 用 taiji 的指数衰减加权评估整体状态（而非简单计数）
    inner_score = calculate_inner_score(recent_calls)

    # 同参数连续失败检查（精确匹配，不受衰减影响）
    same_args_failures = _count_same_args_failures(tool_name, args, recent_calls)

    # ═══ 老阴判定：同参数连续失败≥2次 ═══
    if same_args_failures >= 2:
        return YaoResult(
            yao_type='old_yin',
            recovery_action='rollback',
            should_retry=False,
            should_rollback=True,
            should_ask_user=True,
            should_deposit=False,
            message=f'工具 {tool_name} 使用相同参数已连续失败 {same_args_failures} 次，'
                    f'已标记本回合不可用。建议回滚到安全检查点，请提供新指令。'
        )

    # ═══ 老阴判定：内卦分数极低（指数衰减加权后仍为危机）═══
    if inner_score < -0.2:
        return YaoResult(
            yao_type='old_yin',
            recovery_action='rollback',
            should_retry=False,
            should_rollback=True,
            should_ask_user=True,
            should_deposit=False,
            message=f'工具 {tool_name} 近期整体状态极差（评分 {inner_score:.2f}），'
                    f'建议暂停使用，请提供替代方案。'
        )

    # ═══ 少阴判定：偶发失败，可以重试 ═══
    retry_params = _suggest_retry_params(tool_name, args, error_message)

    # 判断是否是网络/超时类错误（可重试）
    is_retryable = _is_retryable_error(error_message)

    if is_retryable:
        return YaoResult(
            yao_type='young_yin',
            recovery_action='retry',
            should_retry=True,
            should_rollback=False,
            should_ask_user=False,
            should_deposit=False,
            message=f'工具 {tool_name} 偶发失败（{error_message}），'
                    f'正在调整参数重试。',
            retry_params=retry_params
        )
    else:
        # 非可重试错误（如权限不足、资源不存在），直接请求用户
        return YaoResult(
            yao_type='young_yin',
            recovery_action='retry',
            should_retry=False,
            should_rollback=False,
            should_ask_user=True,
            should_deposit=False,
            message=f'工具 {tool_name} 失败：{error_message}。'
                    f'无法自动恢复，请检查后重试。'
        )


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _is_surplus_result(tool_name: str, args: dict, result: str) -> bool:
    """
    判断工具返回是否"超预期"（老阳）。

    规则：返回结果中包含的 key 信息，超出了请求参数的范围。
    简化实现：检查返回结果的长度是否远超预期（>3倍）。
    """
    if not result or not args:
        return False

    # 预期结果长度：参数值的总长度 * 2（合理扩展）
    expected_len = sum(len(str(v)) for v in args.values()) * 2
    actual_len = len(result)

    # 超出 3 倍且至少多 200 字符
    if actual_len > expected_len * 3 and actual_len - expected_len > 200:
        return True

    return False


def _count_same_args_failures(tool_name: str, args: dict, recent_calls: List[dict]) -> int:
    """统计最近同一工具、同一参数的连续失败次数"""
    if not args:
        return 0

    count = 0
    args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)

    for call in recent_calls:
        if call.get('tool_name') != tool_name:
            continue
        call_args = call.get('args_json', '')
        if not call.get('success', 1):
            # 简化比较：检查核心参数是否一致
            if _args_match(args_str, call_args):
                count += 1
        else:
            break  # 遇到成功就中断连续计数

    return count


def _args_match(args_str: str, call_args_json: str) -> bool:
    """简化的参数匹配（核心参数一致即可）"""
    if not call_args_json:
        return False
    try:
        # 提取核心参数做比较
        a = json.loads(args_str) if args_str.startswith('{') else {}
        b = json.loads(call_args_json) if call_args_json.startswith('{') else {}
        # 比较前 3 个核心参数
        keys = list(a.keys())[:3]
        return all(a.get(k) == b.get(k) for k in keys) if keys else False
    except (json.JSONDecodeError, TypeError):
        return args_str == call_args_json


def _is_retryable_error(error_message: str) -> bool:
    """判断错误是否可重试（网络/超时类）"""
    if not error_message:
        return True  # 无错误信息，默认可重试

    retryable_keywords = [
        'timeout', '超时', 'timed out',
        'connection', '连接', '网络', 'network',
        'temporary', '临时', 'retry',
        '503', '502', '500', '429',
    ]
    error_lower = error_message.lower()
    return any(kw in error_lower for kw in retryable_keywords)


def _suggest_retry_params(tool_name: str, args: dict, error_message: str) -> dict:
    """根据错误类型建议重试参数调整"""
    params = dict(args) if args else {}

    error_lower = (error_message or '').lower()

    # 超时 → 增加超时时间
    if 'timeout' in error_lower or '超时' in error_lower:
        if 'timeout' in params:
            params['timeout'] = min(params['timeout'] * 2, 60)
        else:
            params['timeout'] = 30

    # 限流 → 增加延迟
    if '429' in error_lower or 'rate' in error_lower:
        params['_retry_delay'] = 2

    return params


# ═══════════════════════════════════════════════════════════
# 执行变爻后的恢复动作
# ═══════════════════════════════════════════════════════════

def execute_recovery(yao_result: YaoResult, tool_call_id: int = None) -> dict:
    """
    执行变爻判定后的恢复动作。

    Args:
        yao_result: 变爻判定结果
        tool_call_id: 原始工具调用的 ID（用于更新日志）

    Returns:
        恢复动作的执行结果
    """
    result = {
        'yao_type': yao_result.yao_type,
        'recovery_action': yao_result.recovery_action,
        'executed': False,
        'message': yao_result.message
    }

    # 更新工具调用日志
    if tool_call_id:
        log.update_tool_call_yao(
            tool_call_id=tool_call_id,
            yao_type=yao_result.yao_type,
            recovery_action=yao_result.recovery_action
        )

    if yao_result.should_rollback:
        result['executed'] = True
        result['action'] = 'rollback_triggered'
        # 实际回滚逻辑由上游模块（conversation.py）执行

    elif yao_result.should_retry:
        result['executed'] = True
        result['action'] = 'retry_triggered'
        result['retry_params'] = yao_result.retry_params

    elif yao_result.should_deposit:
        result['executed'] = True
        result['action'] = 'deposited'
        # 高价值路径标记，供后续 skill_pairs 沉淀参考

    elif yao_result.should_ask_user:
        result['executed'] = True
        result['action'] = 'ask_user'

    else:
        result['action'] = 'continue'

    return result


def format_yao_message(yao_result: YaoResult) -> str:
    """
    格式化变爻消息（给用户看的）。

    按老师的检查点要求：每次变爻决策后，必须向用户说明调整原因。
    """
    yao_symbols = {
        'old_yang':   '⚌ 老阳',
        'young_yang': '⚎ 少阳',
        'young_yin':  '⚍ 少阴',
        'old_yin':    '⚏ 老阴',
    }

    symbol = yao_symbols.get(yao_result.yao_type, '?')
    return f"[变爻 {symbol}] {yao_result.message}"
