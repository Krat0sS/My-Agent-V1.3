"""
意图路由引擎 — 分类 → 技能匹配 → 执行/分解

这是 My-Agent 的"大脑皮层"。
用户说一句话，路由引擎决定：
1. 简单指令 → 直接调工具
2. 匹配到已有技能 → 极速执行（省 token）
3. 没匹配到 → 分解任务 → 执行 → 自动生成新技能
"""
import json
import re
import time
import asyncio
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass

from skills.loader import Skill, load_all_skills
from tools.registry import registry
from data import execution_log


@dataclass
class RoutingResult:
    """路由决策结果"""
    complexity: str              # "simple" / "medium" / "complex"
    matched_skill: Optional[Skill] = None
    match_score: float = 0.0
    candidates: List[Tuple[str, float]] = None  # [(skill_name, score), ...]
    action: str = ""             # "direct_tool" / "execute_skill" / "decompose"

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


# ═══ 复杂度分类 ═══

# 简单指令特征（一步就能完成）
_SIMPLE_PATTERNS = [
    r'^(打开|关闭|启动|退出|查看|搜索|搜一下|帮我搜)',
    r'^(截图|截屏|拍照)',
    r'^(记住|回忆|记录)',
    r'^(读取|打开|列出)',
]

# 复杂任务特征（需要多步分解）
_COMPLEX_KEYWORDS = [
    '然后', '接着', '之后', '再', '并且', '同时', '先',
    '最后', '整理', '分类', '批量', '全部', '所有',
    '研究', '分析', '对比', '总结', '写一份', '做个报告',
]


def classify_complexity(user_input: str) -> str:
    """
    判断用户意图的复杂度。
    simple  — 一句话就能搞定（"打开百度"）
    medium  — 需要一个技能流程（"整理桌面"）
    complex — 需要分解成多个子任务（"帮我研究AI最新进展写个报告"）
    """
    text = user_input.strip().lower()

    # 简单指令：短 + 匹配简单模式
    if len(text) < 15:
        for pattern in _SIMPLE_PATTERNS:
            if re.match(pattern, text):
                return "simple"

    # 复杂任务：包含多个动作关键词
    complex_count = sum(1 for kw in _COMPLEX_KEYWORDS if kw in text)
    if complex_count >= 2 or len(text) > 50:
        return "complex"

    return "medium"


# ═══ 技能匹配 ═══

def match_skill(user_input: str, skills: List[Skill],
                threshold: float = 0.4) -> Tuple[Optional[Skill], float, List[Tuple[str, float]]]:
    """
    将用户输入与已加载的技能做匹配。

    使用两阶段过滤：
    1. 快速关键词匹配 → 筛出 Top-5 候选
    2. 对 Top-5 做精细评分

    返回: (最佳技能, 置信度分数, 所有候选列表)
    """
    if not skills:
        return None, 0.0, []

    # 第一阶段：关键词匹配（快，毫秒级），使用 jieba 分词
    try:
        import jieba
        user_keywords = set(w.strip() for w in jieba.cut(user_input.lower()) if len(w.strip()) > 1)
    except ImportError:
        user_keywords = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', user_input.lower()))

    scored = []
    for skill in skills:
        # 计算关键词重叠度
        skill_keywords = set(skill.keywords)
        if not skill_keywords:
            continue

        overlap = user_keywords & skill_keywords
        if not overlap:
            scored.append((skill.name, 0.0))
            continue

        # 命中率（用户关键词被覆盖的比例）为主，Jaccard 为辅
        hit_rate = len(overlap) / len(user_keywords) if user_keywords else 0
        jaccard = len(overlap) / len(user_keywords | skill_keywords)
        # 70% 命中率 + 30% Jaccard，让短输入也能得到合理分数
        score = hit_rate * 0.7 + jaccard * 0.3

        scored.append((skill.name, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top5 = scored[:5]

    if not top5 or top5[0][1] < threshold:
        return None, top5[0][1] if top5 else 0.0, top5

    # 返回最佳匹配
    best_name, best_score = top5[0]
    best_skill = next((s for s in skills if s.name == best_name), None)

    return best_skill, best_score, top5


# ═══ 任务分解 ═══

DECOMPOSE_SYSTEM_PROMPT = """你是一个任务规划专家。把用户指令分解为明确的执行步骤。

输出 JSON 格式：
{
  "goal": "最终目标",
  "steps": [
    {"id": 1, "action": "步骤描述", "tool": "建议使用的工具名", "depends_on": []}
  ],
  "skill_name": "建议的技能名称（英文短横线格式，如 web-research）",
  "skill_goal": "这个技能的一句话目标描述"
}

规则：
1. 每步只做一件事
2. 步骤数 2-8 步
3. 用 depends_on 表示步骤依赖
4. 如果这个任务以后可能重复做，给出 skill_name 和 skill_goal"""


async def decompose_task(user_input: str) -> dict:
    """用 LLM 将复杂任务分解为执行计划"""
    from core.llm import chat

    messages = [
        {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

    result = await chat(messages, temperature=0.1)

    if result.get("_error") or result.get("_timeout"):
        return {"goal": user_input, "steps": [], "error": result.get("content", "")}

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
            "goal": user_input,
            "steps": [{"id": 1, "action": content, "tool": "auto", "depends_on": []}],
            "error": "规划解析失败",
        }

    return plan


# ═══ 技能自动生成 ═══

SKILL_GEN_PROMPT = """根据以下任务执行记录，生成一个可复用的技能文档。

任务: {user_input}
执行步骤: {steps_json}

请输出一个 SKILL.md 的内容，格式如下：

# 技能名: [简短描述]
## 目标
[一句话描述该技能要达成的最终结果]
## 前置工具
[列出执行此技能必须调用的工具名]
## 执行步骤
1. [步骤1]
2. [步骤2]
...
## 陷阱与检查点
- [易出错点1]
- [重要验证点2]

规则：
1. 步骤要通用化，不要包含具体的文件路径或搜索关键词
2. 用占位符替代具体值，如 [搜索关键词]、[目标目录]
3. 陷阱要基于实际执行中可能遇到的问题"""


async def generate_skill_md(user_input: str, plan: dict, results: list) -> Optional[str]:
    """从成功的任务执行中提炼 SKILL.md"""
    from core.llm import chat

    steps_json = json.dumps(plan.get("steps", []), ensure_ascii=False, indent=2)

    prompt = SKILL_GEN_PROMPT.format(
        user_input=user_input,
        steps_json=steps_json,
    )

    messages = [
        {"role": "system", "content": "你是一个技能文档生成器。生成简洁、通用、可复用的 SKILL.md。"},
        {"role": "user", "content": prompt},
    ]

    result = await chat(messages, temperature=0.3)

    if result.get("_error") or result.get("_timeout"):
        return None

    return result.get("content", "")


def save_skill(skill_name: str, skill_md_content: str, skills_dir: str = None) -> str:
    """保存新技能到 skills/ 目录"""
    import os
    import config

    if skills_dir is None:
        skills_dir = os.path.join(config.WORKSPACE, "skills")

    skill_dir = os.path.join(skills_dir, skill_name)
    os.makedirs(skill_dir, exist_ok=True)

    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md_path, "w", encoding="utf-8") as f:
        f.write(skill_md_content)

    return skill_md_path


# ═══ 主路由函数 ═══

async def route(user_input: str, skills: List[Skill] = None,
                on_progress=None) -> RoutingResult:
    """
    主路由函数。

    1. 分类复杂度
    2. 如果是 medium，尝试匹配已有技能
    3. 返回路由决策

    注意：这个函数只做决策，不做执行。
    执行由调用方（conversation.py）负责。
    """
    if skills is None:
        skills = load_all_skills()

    complexity = classify_complexity(user_input)

    # simple → 直接调工具，不需要技能
    if complexity == "simple":
        return RoutingResult(
            complexity="simple",
            action="direct_tool",
        )

    # medium → 尝试匹配技能
    if complexity == "medium":
        skill, score, candidates = match_skill(user_input, skills)

        # 记录路由决策
        execution_log.log_routing_decision(
            user_input,
            candidates=[{"skill": name, "score": round(s, 3)} for name, s in candidates],
            chosen_skill=skill.name if skill else None,
            chosen_score=score,
            fallback_to_decompose=(skill is None),
        )

        if skill and score >= 0.4:
            return RoutingResult(
                complexity="medium",
                matched_skill=skill,
                match_score=score,
                candidates=candidates,
                action="execute_skill",
            )
        else:
            return RoutingResult(
                complexity="medium",
                match_score=score,
                candidates=candidates,
                action="decompose",
            )

    # complex → 分解
    return RoutingResult(
        complexity="complex",
        action="decompose",
    )
