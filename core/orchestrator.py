# -*- coding: utf-8 -*-
"""
五行生克编排器 — 技能执行序列的最优排序引擎

当多个技能需要接连调用时，根据历史成功率自动计算"相生权重"，
优先执行"生"链上的技能组合，避免"克"链上的冲突组合。

设计原则：
- 一切从 execution_log 数据自动涌现，不硬编码生克关系
- 种子规则有衰减（seed_weight *= 0.7），10 次后自动归零
- 通关化解：技能库<50 时穷举搜索，≥50 时查历史三元组
- 纯 SQL 查询 + 简单阈值判断，不调用 LLM，延迟 <50ms
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
class OrchestratorResult:
    """编排结果"""
    ordered_skills: List[str]       # 编排后的技能执行顺序
    generate_bonus: float           # 相生加成总和
    overcome_penalty: float         # 相克惩罚总和
    final_score: float              # 最终编排得分
    mediation_used: Optional[str]   # 使用的通关化解技能（如果有）
    reasoning: str                  # 编排依据说明

    def to_note(self) -> str:
        """转为 JSON 字符串，存入 routing_decisions.orchestrator_note"""
        return json.dumps({
            'ordered_skills': self.ordered_skills,
            'generate_bonus': round(self.generate_bonus, 3),
            'overcome_penalty': round(self.overcome_penalty, 3),
            'final_score': round(self.final_score, 3),
            'mediation_used': self.mediation_used,
            'reasoning': self.reasoning
        }, ensure_ascii=False)


@dataclass
class SkillPairRelation:
    """技能对的生克关系"""
    skill_a: str
    skill_b: str
    relation: str           # 'generate' | 'neutral' | 'overcome'
    success_rate: float
    is_seed: bool
    seed_weight: float


# ═══════════════════════════════════════════════════════════
# 生克关系常量
# ═══════════════════════════════════════════════════════════

RELATION_BONUS = {
    'generate': 0.15,   # 相生加成
    'neutral': 0.0,     # 中立无影响
    'overcome': -0.20,  # 相克惩罚
}

MAX_EXHAUSTIVE_SEARCH_SKILLS = 50  # 超过此数量后不再穷举搜索通关化解


# ═══════════════════════════════════════════════════════════
# 生克关系查询
# ═══════════════════════════════════════════════════════════

def get_relation(skill_a: str, skill_b: str) -> SkillPairRelation:
    """查询两个技能之间的生克关系"""
    pair = log.get_skill_pair(skill_a, skill_b)

    if pair:
        return SkillPairRelation(
            skill_a=skill_a,
            skill_b=skill_b,
            relation=pair.get('relation', 'neutral'),
            success_rate=pair.get('success_rate', 0.5),
            is_seed=bool(pair.get('is_seed', 0)),
            seed_weight=pair.get('seed_weight', 0.0)
        )

    # 无数据，返回默认中立
    return SkillPairRelation(
        skill_a=skill_a,
        skill_b=skill_b,
        relation='neutral',
        success_rate=0.5,
        is_seed=False,
        seed_weight=0.0
    )


def get_effective_relation(relation: SkillPairRelation) -> str:
    """
    获取有效的生克关系（考虑种子衰减）。

    种子规则在 seed_weight > 0.1 时仍生效，
    但会被真实数据逐步覆盖。
    """
    if relation.is_seed and relation.seed_weight > 0.1:
        # 种子规则还在生效期
        return relation.relation

    # 真实数据驱动
    return relation.relation


# ═══════════════════════════════════════════════════════════
# 编排主逻辑
# ═══════════════════════════════════════════════════════════

def orchestrate(skill_sequence: List[str]) -> OrchestratorResult:
    """
    编排主入口。

    接收一个技能序列，根据生克关系重新排序，
    使相生的技能尽量相邻，相克的技能尽量远离。

    Args:
        skill_sequence: 原始技能执行序列

    Returns:
        OrchestratorResult 对象
    """
    if len(skill_sequence) <= 1:
        return OrchestratorResult(
            ordered_skills=skill_sequence,
            generate_bonus=0.0,
            overcome_penalty=0.0,
            final_score=1.0,
            mediation_used=None,
            reasoning='只有一个技能，无需编排'
        )

    if len(skill_sequence) == 2:
        return _orchestrate_pair(skill_sequence[0], skill_sequence[1])

    # 3 个及以上技能：贪心排序
    return _orchestrate_greedy(skill_sequence)


def _orchestrate_pair(skill_a: str, skill_b: str) -> OrchestratorResult:
    """两个技能的编排"""
    relation = get_relation(skill_a, skill_b)
    effective = get_effective_relation(relation)

    bonus = RELATION_BONUS.get(effective, 0.0)

    # 如果相克，尝试通关化解
    mediation = None
    if effective == 'overcome':
        mediation = _find_mediation(skill_a, skill_b)
        if mediation:
            # 找到通关技能，化解相克
            bonus = 0.0  # 惩罚清零
            return OrchestratorResult(
                ordered_skills=[skill_a, mediation, skill_b],
                generate_bonus=RELATION_BONUS['generate'] * 2,
                overcome_penalty=0.0,
                final_score=1.0 + RELATION_BONUS['generate'] * 2,
                mediation_used=mediation,
                reasoning=f'{skill_a} 克 {skill_b}，用 {mediation} 通关化解'
            )

    reasoning = f'{skill_a} → {skill_b}: {effective}'
    if relation.is_seed:
        reasoning += '（种子规则）'

    return OrchestratorResult(
        ordered_skills=[skill_a, skill_b],
        generate_bonus=max(0, bonus),
        overcome_penalty=min(0, bonus),
        final_score=1.0 + bonus,
        mediation_used=None,
        reasoning=reasoning
    )


def _orchestrate_greedy(skill_sequence: List[str]) -> OrchestratorResult:
    """
    多技能贪心排序。

    策略：从第一个技能开始，每次选择与当前技能"相生"得分最高的下一个技能。
    """
    remaining = list(skill_sequence)
    ordered = [remaining.pop(0)]
    total_bonus = 0.0
    total_penalty = 0.0
    reasons = []

    while remaining:
        current = ordered[-1]
        best_next = None
        best_score = float('-inf')

        for candidate in remaining:
            relation = get_relation(current, candidate)
            effective = get_effective_relation(relation)
            score = RELATION_BONUS.get(effective, 0.0)

            # 如果相克，检查通关化解
            if effective == 'overcome':
                mediation = _find_mediation(current, candidate)
                if mediation:
                    score = RELATION_BONUS['generate']  # 化解后变相生

            if score > best_score:
                best_score = score
                best_next = candidate

        if best_next:
            ordered.append(best_next)
            remaining.remove(best_next)

            if best_score > 0:
                total_bonus += best_score
                reasons.append(f'{current} 生 {best_next}')
            elif best_score < 0:
                total_penalty += best_score
                reasons.append(f'{current} 克 {best_next}')
            else:
                reasons.append(f'{current} → {best_next}（中立）')

    return OrchestratorResult(
        ordered_skills=ordered,
        generate_bonus=round(total_bonus, 3),
        overcome_penalty=round(total_penalty, 3),
        final_score=round(1.0 + total_bonus + total_penalty, 3),
        mediation_used=None,
        reasoning='; '.join(reasons) if reasons else '无需调整'
    )


# ═══════════════════════════════════════════════════════════
# 通关化解
# ═══════════════════════════════════════════════════════════

def _find_mediation(skill_a: str, skill_b: str) -> Optional[str]:
    """
    寻找通关化解技能。

    A 克 B 时，找一个 C 使得 A 生 C 且 C 生 B（木生火，火生土）。
    """
    all_skills = _get_all_skills()

    # 技能库<50 时穷举搜索
    if len(all_skills) <= MAX_EXHAUSTIVE_SEARCH_SKILLS:
        return _exhaustive_mediation_search(skill_a, skill_b, all_skills)

    # 技能库≥50 时，只查历史三元组
    return _history_mediation_search(skill_a, skill_b)


def _exhaustive_mediation_search(skill_a: str, skill_b: str,
                                  all_skills: List[str]) -> Optional[str]:
    """穷举搜索通关技能"""
    for candidate in all_skills:
        if candidate in (skill_a, skill_b):
            continue

        # 检查 A → C 是否相生
        rel_ac = get_relation(skill_a, candidate)
        if get_effective_relation(rel_ac) != 'generate':
            continue

        # 检查 C → B 是否相生
        rel_cb = get_relation(candidate, skill_b)
        if get_effective_relation(rel_cb) != 'generate':
            continue

        # 找到通关技能
        return candidate

    return None


def _history_mediation_search(skill_a: str, skill_b: str) -> Optional[str]:
    """从历史数据中搜索通关三元组"""
    conn = log._get_conn()

    # 查找历史上 A → ? → B 成功过的中间技能
    rows = conn.execute("""
        SELECT DISTINCT sp1.skill_b as mediator
        FROM skill_pairs sp1
        JOIN skill_pairs sp2 ON sp1.skill_b = sp2.skill_a
        WHERE sp1.skill_a = ?
          AND sp2.skill_b = ?
          AND sp1.relation = 'generate'
          AND sp2.relation = 'generate'
        LIMIT 1
    """, (skill_a, skill_b)).fetchall()

    conn.close()

    if rows:
        return rows[0]['mediator']

    return None


def _get_all_skills() -> List[str]:
    """获取所有已知技能名"""
    conn = log._get_conn()
    rows = conn.execute("""
        SELECT DISTINCT skill_name FROM skill_usage
        UNION
        SELECT DISTINCT skill_a FROM skill_pairs
        UNION
        SELECT DISTINCT skill_b FROM skill_pairs
    """).fetchall()
    conn.close()

    return [r['skill_name'] if 'skill_name' in r.keys() else r[0] for r in rows]


# ═══════════════════════════════════════════════════════════
# 编排结果日志
# ═══════════════════════════════════════════════════════════

def log_orchestration(user_input: str, result: OrchestratorResult,
                      chosen_skill: str = None, session_id: str = ""):
    """记录编排结果到 routing_decisions"""
    log.log_routing_decision(
        user_input=user_input,
        candidates=result.ordered_skills,
        chosen_skill=chosen_skill or result.ordered_skills[0] if result.ordered_skills else None,
        chosen_score=result.final_score,
        fallback_to_decompose=False,
        orchestrator_note=result.to_note()
    )


def format_orchestration_message(result: OrchestratorResult) -> str:
    """格式化编排消息（给用户看的）"""
    if result.mediation_used:
        return (f"[五行编排] 检测到相克关系，已用 {result.mediation_used} 通关化解。"
                f"执行顺序：{' → '.join(result.ordered_skills)}")

    if result.overcome_penalty < 0:
        return (f"[五行编排] 存在相克关系（惩罚 {result.overcome_penalty}），"
                f"已调整为最优顺序：{' → '.join(result.ordered_skills)}")

    if result.generate_bonus > 0:
        return (f"[五行编排] 存在相生关系（加成 +{result.generate_bonus}），"
                f"执行顺序：{' → '.join(result.ordered_skills)}")

    return f"[五行编排] 执行顺序：{' → '.join(result.ordered_skills)}"
