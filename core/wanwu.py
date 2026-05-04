# -*- coding: utf-8 -*-
"""
万物生成器 — "八卦生万物"的技能组合引擎

当无单一技能匹配时，将手头已有技能进行"相错"组合，
生成临时执行计划来完成任务。

设计原则：
- 生成临时 JSON 执行计划，不生成 SKILL.md（宪法 vs 合同）
- 同一组合成功 ≥3 次后，自动沉淀为正式技能
- 乾坤识别：从 skill_pairs 数据驱动，冷启动用启发式
- 上下文隔离：执行完只向主上下文返回摘要，细节留在表里
- 零 API 调用（组合逻辑纯规则，不需要 LLM）
"""
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from data import execution_log as log


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class WanwuPlan:
    """万物生成的临时执行计划"""
    skill_a: str                        # 乾（发起技能）
    skill_b: str                        # 坤（承载技能）
    steps: List[Dict[str, Any]]         # 执行步骤序列
    pattern: str = '乾-坤'              # 组合模式
    estimated_tokens: int = 0           # 预估 token 消耗
    risk_level: str = 'low'             # 风险等级
    plan_id: str = ''                   # 计划 ID

    def to_json(self) -> str:
        return json.dumps({
            'plan_id': self.plan_id,
            'skill_a': self.skill_a,
            'skill_b': self.skill_b,
            'pattern': self.pattern,
            'steps': self.steps,
            'estimated_tokens': self.estimated_tokens,
            'risk_level': self.risk_level
        }, ensure_ascii=False, indent=2)

    def summary(self) -> str:
        """返回给主上下文的简短摘要（上下文隔离）"""
        step_names = [s.get('action', s.get('skill', '?')) for s in self.steps]
        return f"万物组合 [{self.skill_a} → {self.skill_b}]: {' → '.join(step_names)}"


@dataclass
class PromotionCandidate:
    """可升级为正式技能的组合"""
    skill_a: str
    skill_b: str
    success_count: int
    first_plan_id: int


# ═══════════════════════════════════════════════════════════
# 乾坤识别
# ═══════════════════════════════════════════════════════════


def identify_qian_kun(available_skills: List[str]) -> List[Tuple[str, str]]:
    """
    从候选技能中识别所有可能的乾坤配对。

    优先从 skill_pairs 数据驱动（成功率>0.5）。
    冷启动期（数据<10条）：按注册顺序配对，第一个=乾，第二个=坤。
    不用关键词猜测，简单可靠。

    返回: [(qian, kun), ...] 的配对列表
    """
    if len(available_skills) < 2:
        return []

    # 尝试从 skill_pairs 获取数据驱动的配对
    all_pairs = log.get_all_skill_pairs()
    data_driven = set()
    total_data_points = sum(p.get('total_calls', 0) for p in all_pairs)

    # 数据充足时（≥10条真实调用）用数据驱动
    if total_data_points >= 10:
        for pair in all_pairs:
            a, b = pair['skill_a'], pair['skill_b']
            if a in available_skills and b in available_skills:
                if pair['success_rate'] > 0.5:
                    data_driven.add((a, b))

        if data_driven:
            return list(data_driven)

    # 冷启动：按注册顺序配对（简单可靠，不会因关键词匹配出错）
    pairs = []
    for i, qian in enumerate(available_skills):
        for kun in available_skills[i + 1:]:
            pairs.append((qian, kun))

    return pairs


# ═══════════════════════════════════════════════════════════
# 计划生成
# ═══════════════════════════════════════════════════════════

def _build_plan_steps(skill_a: str, skill_b: str, user_input: str) -> List[Dict[str, Any]]:
    """
    构建执行步骤序列。

    简化实现：两步组合（A 发起，B 承载）。
    后续可以扩展为三步、四步组合。
    """
    steps = [
        {
            'step': 1,
            'skill': skill_a,
            'action': f'调用 {skill_a} 完成信息获取/发起阶段',
            'params': {'user_input': user_input},
            'role': 'qian'  # 乾：发起
        },
        {
            'step': 2,
            'skill': skill_b,
            'action': f'调用 {skill_b} 完成承载/落地阶段',
            'params': {},  # 参数由上一步结果动态填充
            'role': 'kun'  # 坤：承载
        }
    ]
    return steps


def wanwu_generate(user_input: str, available_skills: List[str],
                   session_id: str = "") -> Optional[WanwuPlan]:
    """
    万物生成主入口。

    当无单一技能匹配时，尝试组合现有技能生成临时执行计划。

    Args:
        user_input: 用户输入
        available_skills: 当前可用的技能列表
        session_id: 会话 ID

    Returns:
        WanwuPlan 对象，或 None（无法组合）
    """
    if len(available_skills) < 2:
        return None  # 技能不足，无法组合

    # 识别乾坤配对
    qian_kun_pairs = identify_qian_kun(available_skills)

    if not qian_kun_pairs:
        return None  # 无法配对

    # 选择最佳配对：优先选 skill_pairs 中成功率最高的
    best_pair = _select_best_pair(qian_kun_pairs)

    if not best_pair:
        return None

    skill_a, skill_b = best_pair

    # 构建执行计划
    steps = _build_plan_steps(skill_a, skill_b, user_input)

    plan = WanwuPlan(
        skill_a=skill_a,
        skill_b=skill_b,
        steps=steps,
        pattern='乾-坤',
        estimated_tokens=len(user_input) * 3,  # 粗略估算
        risk_level='low',
        plan_id=f"wanwu_{int(time.time())}_{hash(user_input) % 1000:03d}"
    )

    # 记录到 wanwu_plans 表
    log.log_wanwu_plan(
        user_input=user_input,
        skill_a=skill_a,
        skill_b=skill_b,
        plan_json=plan.to_json(),
        success=False,
        session_id=session_id
    )

    return plan


def _select_best_pair(pairs: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """选择最佳乾坤配对"""
    best_pair = None
    best_score = -1

    for a, b in pairs:
        pair_info = log.get_skill_pair(a, b)
        if pair_info:
            # 优先选成功率高的
            score = pair_info.get('success_rate', 0.5)
            # 种子规则有额外加成
            if pair_info.get('is_seed', 0):
                score += pair_info.get('seed_weight', 0)
        else:
            # 无历史数据，默认分
            score = 0.5

        if score > best_score:
            best_score = score
            best_pair = (a, b)

    return best_pair


# ═══════════════════════════════════════════════════════════
# 执行结果反馈
# ═══════════════════════════════════════════════════════════

def record_wanwu_result(plan: WanwuPlan, success: bool,
                        user_feedback: str = None,
                        elapsed_ms: int = 0, token_cost: int = 0):
    """
    记录万物计划的执行结果。

    同时更新 skill_pairs 的生克数据。
    """
    # 更新 wanwu_plans 表
    # 找到最近一条匹配的记录并更新
    log.log_wanwu_plan(
        user_input='',  # 已有记录，这里只是标记成功
        skill_a=plan.skill_a,
        skill_b=plan.skill_b,
        plan_json=plan.to_json(),
        success=success,
        user_feedback=user_feedback,
        elapsed_ms=elapsed_ms,
        token_cost=token_cost
    )

    # 更新 skill_pairs 的生克数据
    log.update_skill_pair(plan.skill_a, plan.skill_b, success)


# ═══════════════════════════════════════════════════════════
# 沉淀检查：临时计划 → 正式技能
# ═══════════════════════════════════════════════════════════

PROMOTION_THRESHOLD = 3  # 成功 3 次后自动沉淀


def check_promotion_candidates() -> List[PromotionCandidate]:
    """检查是否有组合可以升级为正式技能"""
    candidates = log.get_wanwu_promotion_candidates(threshold=PROMOTION_THRESHOLD)
    return [
        PromotionCandidate(
            skill_a=c['skill_a'],
            skill_b=c['skill_b'],
            success_count=c['success_count'],
            first_plan_id=c['first_plan_id']
        )
        for c in candidates
    ]


def promote_to_skill(candidate: PromotionCandidate, skills_dir: str = "skills") -> bool:
    """
    将验证过的组合升级为正式 SKILL.md。

    1. 自动生成 SKILL.md 草稿
    2. 用 maturity = '潜龙勿用' 初始化
    3. 写入 skills/ 目录
    4. 标记 wanwu_plans 为已升级
    """
    import os

    skill_name = f"{candidate.skill_a}+{candidate.skill_b}"
    skill_dir = os.path.join(skills_dir, skill_name)

    # 创建技能目录
    os.makedirs(skill_dir, exist_ok=True)

    # 生成 SKILL.md
    skill_md = f"""# {skill_name}

> 由万物生成器自动沉淀（成功 {candidate.success_count} 次）
> 组合模式：{candidate.skill_a}（乾/发起）→ {candidate.skill_b}（坤/承载）
> 成熟度：潜龙勿用（初始状态，需用户验证后升级）

## 目标
自动组合 `{candidate.skill_a}` 和 `{candidate.skill_b}` 的能力，完成复合任务。

## 执行步骤
1. 调用 `{candidate.skill_a}` 完成信息获取/发起阶段
2. 将结果传递给 `{candidate.skill_b}` 完成承载/落地阶段

## 陷阱与检查点
- 如果第一步失败，不要继续第二步
- 每步执行后检查结果质量，避免垃圾进垃圾出
- 成功 3 次后可考虑升级成熟度为"见龙在田"

## 前置工具
- {candidate.skill_a}
- {candidate.skill_b}

## 来源
- 自动生成自万物生成器 (wanwu-generator)
- 首次组合计划 ID: {candidate.first_plan_id}
"""

    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md_path, 'w', encoding='utf-8') as f:
        f.write(skill_md)

    # 标记为已升级
    for plan_id in range(candidate.first_plan_id, candidate.first_plan_id + candidate.success_count):
        log.mark_wanwu_promoted(plan_id)

    return True


# ═══════════════════════════════════════════════════════════
# 沉淀成熟度管理（与老师讨论的潜龙→飞龙成长曲线）
# ═══════════════════════════════════════════════════════════

MATURITY_LEVELS = {
    '潜龙勿用': {'min_success': 0, 'max_success': 2, 'auto_execute': False, 'description': '刚生成，只建议不执行'},
    '见龙在田': {'min_success': 3, 'max_success': 9, 'auto_execute': False, 'description': '验证过，半自动执行'},
    '飞龙在天': {'min_success': 10, 'max_success': 99, 'auto_execute': True, 'description': '高频命中，全自动静默执行'},
    '亢龙有悔': {'min_success': -99, 'max_success': -1, 'auto_execute': False, 'description': '产生副作用，降级审查'},
}


def get_maturity(success_count: int, failure_count: int = 0) -> str:
    """根据成功/失败次数判定技能成熟度"""
    if failure_count >= 3:
        return '亢龙有悔'  # 连续失败过多，降级

    net = success_count - failure_count

    for level, config in MATURITY_LEVELS.items():
        if config['min_success'] <= net <= config['max_success']:
            return level

    return '潜龙勿用'  # 默认
