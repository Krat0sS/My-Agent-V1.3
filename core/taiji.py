# -*- coding: utf-8 -*-
"""
太极诊断 — Agent 的四象自诊内核

每次对话循环的必经之路（<50ms），在路由决策之前运行。
输出：内卦（Agent 自身状态）+ 外卦（任务环境状态）+ 卦象 + 行动建议。

设计原则：
- 纯规则，零 API 调用
- 16 种组合，字典查表，0ms 延迟
- 结果写入 diagnosis_log 表
"""
import time
import re
from dataclasses import dataclass
from typing import List, Optional
from data import execution_log as log
from tools.registry import registry


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class Diagnosis:
    """太极诊断结果"""
    inner: str           # 内卦四象: 'old_yang' | 'young_yang' | 'young_yin' | 'old_yin'
    outer: str           # 外卦四象: 同上
    inner_score: float   # 内卦原始分数 [-1, 1]
    outer_score: float   # 外卦原始分数 [0, 1]
    hexagram: str        # 卦名
    action_hint: str     # 行动建议

    def __str__(self):
        return f"[{self.hexagram}] 内={self.inner} 外={self.outer} → {self.action_hint}"


# ═══════════════════════════════════════════════════════════
# 卦象查表：16 种组合
# ═══════════════════════════════════════════════════════════

HEXAGRAM_ACTION = {
    # (inner, outer) → (卦名, 行动建议)
    ('old_yang', 'old_yang'):    ('乾为天',      'full_execute'),       # 全速执行
    ('old_yang', 'young_yang'):  ('天风姤',      'execute_with_watch'), # 执行但监控
    ('old_yang', 'young_yin'):   ('天山遁',      'execute_partial'),    # 部分执行，留退路
    ('old_yang', 'old_yin'):     ('天地否',      'pause_ask'),          # 暂停，问用户
    ('young_yang', 'old_yang'):  ('风天小畜',    'execute_normal'),     # 正常执行
    ('young_yang', 'young_yang'):('巽为风',      'execute_cautious'),   # 谨慎执行
    ('young_yang', 'young_yin'): ('风山渐',      'step_by_step'),       # 分步试探
    ('young_yang', 'old_yin'):   ('风地观',      'observe_first'),      # 先观察不动手
    ('young_yin', 'old_yang'):   ('山天大畜',    'retry_then_execute'), # 重试一次再执行
    ('young_yin', 'young_yang'): ('山风蛊',      'fix_then_continue'),  # 修复后继续
    ('young_yin', 'young_yin'):  ('艮为山',      'stop_analyze'),       # 停下来分析
    ('young_yin', 'old_yin'):    ('山地剥',      'rollback_request'),   # 回滚并请求指导
    ('old_yin', 'old_yang'):     ('地天泰',      'recover_easy'),       # 环境好，快速恢复
    ('old_yin', 'young_yang'):   ('地风升',      'recover_step'),       # 分步恢复
    ('old_yin', 'young_yin'):    ('地山谦',      'humble_rollback'),    # 谦虚回滚
    ('old_yin', 'old_yin'):      ('坤为地',      'full_stop'),          # 完全停止，等用户
}

# 行动建议的详细说明（给下游模块用）
ACTION_DESCRIPTIONS = {
    'full_execute':      '系统状态极佳，全速执行，无需额外检查',
    'execute_with_watch': '系统正常但有隐患，执行时开启监控',
    'execute_partial':   '环境有风险，只执行安全部分，保留回退路径',
    'pause_ask':         '环境恶劣，暂停执行，向用户确认方向',
    'execute_normal':    '系统正常，按标准流程执行',
    'execute_cautious':  '系统有小问题，谨慎执行，每步验证',
    'step_by_step':      '环境不确定，分步试探，每步确认后再继续',
    'observe_first':     '环境恶劣，先收集信息，不做实际操作',
    'retry_then_execute':'最近有失败但环境好，重试一次后执行',
    'fix_then_continue': '有小问题需要修复，修复后继续主流程',
    'stop_analyze':      '内外都有问题，停下来分析根因',
    'rollback_request':  '问题严重，回滚到安全点并请求用户指导',
    'recover_easy':      '内部状态差但环境好，可以快速恢复',
    'recover_step':      '内部状态差且环境一般，分步恢复',
    'humble_rollback':   '内外都差，谦虚回滚，不强行推进',
    'full_stop':         '全面危机，完全停止，等待外部干预',
}


# ═══════════════════════════════════════════════════════════
# 内卦判定：Agent 自身状态
# ═══════════════════════════════════════════════════════════

def _assess_inner(recent_calls: List[dict]) -> tuple:
    """
    内卦判定：从最近的工具调用记录中评估 Agent 自身状态。

    使用指数衰减加权：最近的成败权重最大，每往前一轮衰减 30%。
    避免固定窗口的误触发问题。

    返回: (state, score)
    """
    score = calculate_inner_score(recent_calls)

    if score > 0.6:
        state = 'old_yang'      # 巅峰：连续成功，权重集中
    elif score > 0.2:
        state = 'young_yang'    # 正常：偶有小挫但整体向好
    elif score > -0.2:
        state = 'young_yin'     # 警觉：最近有失败，需要留意
    else:
        state = 'old_yin'       # 危机：连续失败，需要干预

    return state, score


def calculate_inner_score(recent_calls: List[dict]) -> float:
    """
    计算内卦原始分数（公共接口，供 change_engine 等模块复用）。

    指数衰减加权：最近的成败权重最大，每往前一轮衰减 30%。
    返回: [-1.0, 1.0]
    """
    if not recent_calls:
        return 0.0  # 无历史数据，默认中立

    weight = 1.0
    total_score = 0.0
    max_possible = 0.0

    for call in reversed(recent_calls[:10]):
        success = call.get('success', 1)
        total_score += (1 if success else -1) * weight
        max_possible += weight
        weight *= 0.7

    return total_score / max_possible if max_possible > 0 else 0.0


# ═══════════════════════════════════════════════════════════
# 外卦判定：任务环境状态
# ═══════════════════════════════════════════════════════════

# 动作关键词（从 ToolRegistry 动态生成 + 兜底静态表）
_ACTION_VERBS_CACHE = None

def _get_action_verbs() -> List[str]:
    """从 ToolRegistry 动态构建动词表，自动覆盖所有已注册工具"""
    global _ACTION_VERBS_CACHE
    if _ACTION_VERBS_CACHE is not None:
        return _ACTION_VERBS_CACHE

    verbs = set()
    try:
        for tool in registry.get_all():
            desc = tool.description or ""
            # 从描述中提取第一个中文动词（2-4字）
            match = re.match(r'^([\u4e00-\u9fff]{2,4})', desc.strip())
            if match:
                verbs.add(match.group(1))
    except Exception:
        pass

    # 兜底：如果动态提取失败或结果太少，用静态表补充
    if len(verbs) < 10:
        verbs.update([
            '打开', '搜索', '整理', '创建', '删除', '发送', '分析', '查找',
            '备份', '清理', '关闭', '下载', '上传', '复制', '移动', '重命名',
            '编写', '运行', '安装', '配置', '检查', '监控', '导出', '导入'
        ])

    _ACTION_VERBS_CACHE = list(verbs)
    return _ACTION_VERBS_CACHE


def _refresh_action_verbs():
    """工具注册变更后调用，刷新缓存"""
    global _ACTION_VERBS_CACHE
    _ACTION_VERBS_CACHE = None

# 模糊指令特征
VAGUE_PATTERNS = [
    '帮我', '看看', '弄一下', '搞一下', '处理', '解决',
    '怎么办', '什么', '为什么', '怎么样'
]


def _assess_clarity(user_input: str) -> float:
    """
    指令明确度评估（纯规则，0ms）。

    三层判定：
    1. 长度启发式
    2. 关键词密度（动作动词 + 宾语描述）
    3. 模糊模式检测

    返回: [0, 1]
    """
    if not user_input or len(user_input.strip()) < 2:
        return 0.1  # 几乎为空，极度模糊

    text = user_input.strip()
    score = 0.5  # 基线分

    # 层 1：长度启发式
    if len(text) < 4:
        score -= 0.2  # 太短
    elif len(text) > 10:
        score += 0.1  # 足够长，大概率有具体内容

    # 层 2：动作动词（从 ToolRegistry 动态生成）
    has_verb = any(v in text for v in _get_action_verbs())
    if has_verb:
        score += 0.2

    # 层 3：模糊模式
    is_vague = any(p in text for p in VAGUE_PATTERNS)
    if is_vague and not has_verb:
        score -= 0.3  # 纯模糊指令

    # 宾语长度（超过 5 字符大概率有具体对象）
    words = text.split()
    if len(words) >= 3 or (len(text) > 8 and has_verb):
        score += 0.1

    return max(0.0, min(1.0, score))


def _check_tool_availability(user_input: str) -> float:
    """
    工具可用性评估 v2：用 ToolRegistry 的工具描述做语义匹配。

    核心思路：把用户输入切成 n-gram，和每个工具的 name+description 做交集，
    取最佳匹配分数。不是字符级重叠，是词级重叠。

    返回: [0.3, 1.0]（最低 0.3，不要让"没匹配"拖垮外卦）
    """
    available = [td for td in registry.get_all() if td.is_available()]
    if not available:
        return 0.0

    text = user_input.strip().lower()
    if not text:
        return 0.3

    # 把用户输入切成 2-4 字的滑动窗口（中文没有空格分词）
    def ngrams(s, n):
        return [s[i:i+n] for i in range(len(s)-n+1)]

    user_tokens = set(ngrams(text, 2)) | set(ngrams(text, 3)) | set(ngrams(text, 4))
    # 加上单字，防漏
    user_tokens |= set(text)

    best_score = 0.0

    for td in available:
        tool_text = (td.name + " " + (td.description or "")).lower()
        tool_tokens = set(ngrams(tool_text, 2)) | set(ngrams(tool_text, 3)) | set(tool_text)

        if not tool_tokens:
            continue

        # Jaccard-like: 交集 / 用户token数（不是并集，因为工具描述通常比用户输入长很多）
        intersection = user_tokens & tool_tokens
        score = len(intersection) / max(len(user_tokens), 1)

        best_score = max(best_score, score)

    # 映射到 [0.3, 1.0] 区间（最低 0.3，不要让"没匹配"拖垮外卦）
    return max(0.3, min(1.0, best_score * 2))


def _assess_outer(user_input: str) -> tuple:
    """
    外卦判定：评估当前任务环境状态。

    两个维度：
    - 指令明确度：用户说的清不清楚
    - 工具可用性：需要的工具能不能用

    返回: (state, score)
    """
    clarity = _assess_clarity(user_input)
    tool_ready = _check_tool_availability(user_input)
    score = (clarity + tool_ready) / 2

    if score > 0.7:
        state = 'old_yang'      # 环境完备
    elif score > 0.45:
        state = 'young_yang'    # 基本可用
    elif score > 0.25:
        state = 'young_yin'     # 有障碍
    else:
        state = 'old_yin'       # 环境恶劣

    return state, score


# ═══════════════════════════════════════════════════════════
# 主入口：太极诊断
# ═══════════════════════════════════════════════════════════

def taiji_diagnose(user_input: str, session_id: str = "") -> Diagnosis:
    """
    太极诊断主入口。

    每次对话循环必经，<50ms。
    1. 读取最近工具调用 → 判定内卦
    2. 分析用户输入 → 判定外卦
    3. 查表得卦象和行动建议
    4. 写入 diagnosis_log

    Args:
        user_input: 用户输入文本
        session_id: 会话 ID（可选）

    Returns:
        Diagnosis 对象
    """
    start_time = time.time()

    # 读取最近 10 条工具调用
    recent_calls = log.get_recent_tool_calls(limit=10)

    # 内卦判定
    inner_state, inner_score = _assess_inner(recent_calls)

    # 外卦判定
    outer_state, outer_score = _assess_outer(user_input)

    # 查表得卦象
    key = (inner_state, outer_state)
    hexagram, action_hint = HEXAGRAM_ACTION.get(key, ('未知卦', 'full_stop'))

    elapsed_ms = int((time.time() - start_time) * 1000)

    diagnosis = Diagnosis(
        inner=inner_state,
        outer=outer_state,
        inner_score=round(inner_score, 3),
        outer_score=round(outer_score, 3),
        hexagram=hexagram,
        action_hint=action_hint
    )

    # 写入诊断日志
    log.log_diagnosis(
        inner_state=inner_state,
        outer_state=outer_state,
        inner_score=round(inner_score, 3),
        outer_score=round(outer_score, 3),
        hexagram=hexagram,
        action_hint=action_hint,
        elapsed_ms=elapsed_ms,
        session_id=session_id
    )

    return diagnosis


def get_action_description(action_hint: str) -> str:
    """获取行动建议的详细说明"""
    return ACTION_DESCRIPTIONS.get(action_hint, '未知行动')
