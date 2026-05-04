# -*- coding: utf-8 -*-
"""
时辰感知器 — "天干地支"的节律感知引擎

根据时间段和历史行为调整 Agent 行为策略：
- 时辰-精力映射（上午高认知、午后低认知、晚间创造性）
- 历史高频任务匹配（这个时段用户通常做什么）
- 主动建议（confidence > 0.8 且当天未提醒过才触发）

设计原则：
- 砍掉地支六合，只保留时辰-精力周期（有行为心理学支撑）
- 纯规则，零 API 调用
- 结果写入 tasks 的 time_slot + task_type 列 + time_patterns 表
- 主动建议必须附带可一键拒绝的选项
"""
import datetime
from dataclasses import dataclass
from typing import List, Optional, Dict
from data import execution_log as log


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class TemporalContext:
    """时辰感知上下文"""
    hour: int                # 当前小时（24小时制）
    time_slot: str           # 时辰名（子丑寅卯...）
    time_slot_cn: str        # 时辰中文名
    energy_level: str        # 精力水平：peak/high/medium/decline/low/creative
    day_type: str            # 'weekday' | 'weekend'
    weekday_name: str        # 星期名
    suggestion: Optional[str] = None  # 主动建议（如果有）
    suggestion_task: Optional[str] = None  # 建议的任务类型

    def __str__(self):
        s = f"[{self.time_slot_cn}] 精力={self.energy_level}, {self.weekday_name}"
        if self.suggestion:
            s += f" | 建议: {self.suggestion}"
        return s


# ═══════════════════════════════════════════════════════════
# 时辰-精力映射表
# ═══════════════════════════════════════════════════════════

ENERGY_LEVEL_TASKS = {
    'peak':      ['complex', 'analysis', 'research'],     # 认知巅峰：复杂任务
    'high':      ['complex', 'mechanical', 'creative'],   # 高效：各类任务
    'medium':    ['mechanical', 'maintenance'],            # 中等：机械任务
    'decline':   ['mechanical', 'maintenance'],            # 下降：简单任务
    'low':       ['maintenance', 'social'],                # 低谷：维护类
    'creative':  ['creative', 'brainstorm', 'writing'],    # 创造力窗口
}

WEEKDAY_NAMES = {
    0: '周一', 1: '周二', 2: '周三', 3: '周四',
    4: '周五', 5: '周六', 6: '周日'
}


# ═══════════════════════════════════════════════════════════
# 主入口：时辰感知
# ═══════════════════════════════════════════════════════════

def get_temporal_context() -> TemporalContext:
    """
    时辰感知主入口。

    读取当前时间，生成时辰上下文，
    并检查是否有历史高频任务可以主动建议。

    Returns:
        TemporalContext 对象
    """
    now = datetime.datetime.now()
    hour = now.hour
    weekday = now.weekday()

    time_slot = log.get_time_slot(hour)
    time_slot_cn = log.TIME_SLOT_NAMES.get(time_slot, time_slot)
    energy_level = log.TIME_SLOT_ENERGY.get(time_slot, 'medium')
    day_type = 'weekend' if weekday >= 5 else 'weekday'
    weekday_name = WEEKDAY_NAMES.get(weekday, '?')

    ctx = TemporalContext(
        hour=hour,
        time_slot=time_slot,
        time_slot_cn=time_slot_cn,
        energy_level=energy_level,
        day_type=day_type,
        weekday_name=weekday_name
    )

    # 检查是否有高频任务可以主动建议
    _check_suggestion(ctx)

    return ctx


# ═══════════════════════════════════════════════════════════
# 主动建议
# ═══════════════════════════════════════════════════════════

# 当天已建议过的任务（防重复提醒）
_daily_suggestions: Dict[str, set] = {}


def _check_suggestion(ctx: TemporalContext):
    """检查是否有高频任务可以主动建议"""
    today = datetime.date.today().isoformat()

    # 初始化当天的已建议集合
    if today not in _daily_suggestions:
        _daily_suggestions[today] = set()

    # 查询该时段的高频任务
    patterns = log.get_peak_tasks(
        time_slot=ctx.time_slot,
        day_type=ctx.day_type,
        min_confidence=0.6
    )

    for pattern in patterns:
        task_type = pattern['task_type']
        confidence = pattern.get('confidence', 0)

        # 跳过今天已建议过的
        if task_type in _daily_suggestions[today]:
            continue

        # confidence > 0.8 才触发主动建议
        if confidence >= 0.8:
            ctx.suggestion = _format_suggestion(task_type, ctx)
            ctx.suggestion_task = task_type
            _daily_suggestions[today].add(task_type)
            break


def _format_suggestion(task_type: str, ctx: TemporalContext) -> str:
    """格式化主动建议"""
    task_names = {
        'complex': '处理复杂任务',
        'mechanical': '整理文件或备份',
        'creative': '创意工作或头脑风暴',
        'analysis': '数据分析或研究',
        'research': '搜索和调研',
        'maintenance': '系统维护',
        'writing': '写作或文档',
        'brainstorm': '头脑风暴',
        'social': '沟通和社交',
    }
    task_name = task_names.get(task_type, task_type)
    return f"这个时间你通常会{task_name}，需要我现在帮你处理吗？回复'不用'我就记下。"


# ═══════════════════════════════════════════════════════════
# 时间规律记录
# ═══════════════════════════════════════════════════════════

def record_task_pattern(user_input: str, matched_skill: str = None):
    """
    记录任务的时间规律。

    每次任务执行后调用，更新 time_patterns 表。
    """
    now = datetime.datetime.now()
    time_slot = log.get_time_slot(now.hour)
    day_type = 'weekend' if now.weekday() >= 5 else 'weekday'

    # 从 user_input 推断任务类型
    task_type = _infer_task_type(user_input, matched_skill)

    if task_type:
        log.update_time_pattern(time_slot, day_type, task_type)


def _infer_task_type(user_input: str, matched_skill: str = None) -> Optional[str]:
    """
    从用户输入推断任务类型。

    规则优先，不调 LLM。
    """
    if not user_input:
        return None

    text = user_input.lower()

    # 关键词映射
    type_keywords = {
        'complex':   ['分析', '研究', '设计', '架构', '规划', '优化', '重构'],
        'mechanical': ['整理', '清理', '备份', '移动', '复制', '重命名', '归档'],
        'creative':  ['写', '创作', '画', '设计', '头脑风暴', '创意', '灵感'],
        'analysis':  ['分析', '统计', '对比', '评估', '诊断', '检查'],
        'research':  ['搜索', '查找', '调研', '了解', '学习', '查看'],
        'maintenance': ['安装', '配置', '更新', '修复', '维护', '监控'],
        'writing':   ['写', '文档', '报告', '总结', '笔记', '文章'],
    }

    for task_type, keywords in type_keywords.items():
        if any(kw in text for kw in keywords):
            return task_type

    # 如果有匹配的技能，从技能名推断
    if matched_skill:
        skill_lower = matched_skill.lower()
        if 'search' in skill_lower or 'research' in skill_lower:
            return 'research'
        if 'organize' in skill_lower or 'desktop' in skill_lower:
            return 'mechanical'
        if 'write' in skill_lower or 'create' in skill_lower:
            return 'creative'

    return 'mechanical'  # 默认归类为机械任务


# ═══════════════════════════════════════════════════════════
# 用户反馈处理
# ═══════════════════════════════════════════════════════════

def handle_suggestion_rejection(task_type: str, ctx: TemporalContext):
    """
    用户拒绝了主动建议。

    按老师的设计：在该时段的任务权重上打一个 30 天的衰减标记。
    不是永久压死，而是暂时降低优先级。
    """
    # 更新 time_patterns，降低该任务的频率
    conn = log._get_conn()
    conn.execute("""
        UPDATE time_patterns
        SET frequency = MAX(1, frequency - 3)
        WHERE time_slot=? AND day_type=? AND task_type=?
    """, (ctx.time_slot, ctx.day_type, task_type))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def get_energy_description(energy_level: str) -> str:
    """获取精力水平的描述"""
    descriptions = {
        'peak':     '认知巅峰，适合复杂分析和深度思考',
        'high':     '高效状态，各类任务都能胜任',
        'medium':   '中等精力，适合常规任务',
        'decline':  '精力下降，建议处理简单任务',
        'low':      '低谷期，只做必要的维护工作',
        'creative': '创造力窗口，适合头脑风暴和写作',
    }
    return descriptions.get(energy_level, '未知状态')


def format_temporal_message(ctx: TemporalContext) -> str:
    """格式化时辰消息（给用户看的）"""
    msg = f"[时辰感知] 当前{ctx.time_slot_cn}，精力{ctx.energy_level}"
    if ctx.suggestion:
        msg += f"\n💡 {ctx.suggestion}"
    return msg
