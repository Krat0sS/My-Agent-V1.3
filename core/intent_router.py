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
    '最后', '分类', '批量', '全部', '所有',
    '研究', '分析', '对比', '总结', '写一份', '做个报告',
]

# 工具关键词：命中时应走 direct_tool（让 LLM 用 function calling 调工具）
_TOOL_KEYWORDS = [
    '搜索', '搜一下', '查找', '打开', '运行', '执行', '下载', '上传',
    '截图', '截屏', '整理', '创建', '删除', '备份', '清理', '监控',
    '分析', '移动', '复制', '重命名', '读取', '写入', '编辑',
    '浏览器', '网页', '点击', '输入', '滚动',
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


# ═══ 技能匹配（v1.3.4: BM25 粗筛 + LLM 精排） ═══

# BM25 索引缓存（避免每次匹配都重建）
_bm25_cache = {"skills_hash": None, "index": None}


def _build_bm25_index(skills: List[Skill]):
    """构建 BM25 索引，用技能的 goal + keywords + name 作为文档"""
    from core.bm25 import BM25Index

    # 检查缓存：技能列表没变就复用
    skills_hash = hash(tuple(s.name for s in skills))
    if _bm25_cache["skills_hash"] == skills_hash and _bm25_cache["index"] is not None:
        return _bm25_cache["index"]

    index = BM25Index()
    for skill in skills:
        # 文档 = 目标描述 + 关键词 + 技能名（用空格拼接）
        doc_text = f"{skill.goal} {' '.join(skill.keywords)} {skill.name.replace('-', ' ')}"
        index.add(skill.name, doc_text)
    index.build()

    _bm25_cache["skills_hash"] = skills_hash
    _bm25_cache["index"] = index
    return index


# BM25 分数阈值（基于实测校准）
# BM25 分数受文档数量和 IDF 影响，技能少（3个）时分数普遍偏低
_BM25_HIGH_CONFIDENCE = 2.0   # 高于此分 → 直接命中（无需 LLM）
_BM25_BORDERLINE_LOW = 0.5    # 低于此分 → 认为不匹配
_LLM_CONFIRM_TOP_N = 3       # LLM 精排候选数


async def _llm_confirm_match(user_input: str, skill: Skill) -> Tuple[bool, float]:
    """
    LLM 精排：让 DeepSeek 判断用户输入是否真的匹配这个技能。

    返回: (是否匹配, 置信度 0-1)
    """
    from core.llm import chat

    prompt = f"""判断以下用户输入是否应该使用"{skill.name}"技能来完成。

用户输入: {user_input}
技能目标: {skill.goal}
技能步骤: {chr(10).join(f'{i+1}. {s}' for i, s in enumerate(skill.steps[:5]))}

请只回答一个 JSON：
{{"match": true/false, "confidence": 0.0-1.0, "reason": "一句话理由"}}

判断标准：
- 如果用户意图的核心目标和技能目标一致，match=true
- 如果只是部分相关或不确定，confidence < 0.5
- 不要因为关键词相似就误判，要看意图"""

    messages = [
        {"role": "system", "content": "你是技能匹配判断器。只输出 JSON，不要多余内容。"},
        {"role": "user", "content": prompt},
    ]

    result = await chat(messages, temperature=0.1, use_ollama=True)

    if result.get("_error") or result.get("_timeout"):
        return False, 0.0

    content = result["content"].strip()
    try:
        # 提取 JSON
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content
        data = json.loads(json_str)
        return data.get("match", False), data.get("confidence", 0.0)
    except (json.JSONDecodeError, IndexError):
        return False, 0.0


def match_skill(user_input: str, skills: List[Skill],
                threshold: float = 0.4) -> Tuple[Optional[Skill], float, List[Tuple[str, float]]]:
    """
    v1.3.4: BM25 粗筛替代简单关键词重叠。

    BM25 的核心改进：
    - IDF 自动给稀有词高权重（"PDF" > "文件"，"研究" > "搜索"）
    - TF 归一化避免长文档天然高分
    - 长度归一化消除文档长度偏差

    返回: (最佳技能, BM25 分数, 候选列表)
    注意：返回的 score 是 BM25 原始分，不是 0-1 的概率。
    调用方通过 _BM25_HIGH_CONFIDENCE / _BM25_BORDERLINE_LOW 判断置信度。
    """
    if not skills:
        return None, 0.0, []

    # BM25 粗筛（毫秒级）
    index = _build_bm25_index(skills)
    results = index.search(user_input, top_k=5)

    if not results:
        return None, 0.0, []

    top_name, top_score = results[0]
    top_skill = next((s for s in skills if s.name == top_name), None)

    # 候选列表（带分数）
    candidates = results

    return top_skill, top_score, candidates


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

    result = await chat(messages, temperature=0.1, use_ollama=False)

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
    主路由函数（v1.3.4: BM25 + LLM 精排）。

    决策流程：
    1. 分类复杂度
    2. simple → 直接走 LLM
    3. medium → BM25 粗筛：
       - 高置信 (score >= 3.0) → 直接执行技能
       - 模糊区间 (1.0 <= score < 3.0) → LLM 精排确认
       - 低分 (score < 1.0) → 走 decompose
    4. complex → decompose
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

    # medium → BM25 粗筛 + 可选 LLM 精排
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

        # 高置信：BM25 分数足够高，直接执行
        if skill and score >= _BM25_HIGH_CONFIDENCE:
            return RoutingResult(
                complexity="medium",
                matched_skill=skill,
                match_score=score,
                candidates=candidates,
                action="execute_skill",
            )

        # 模糊区间：LLM 精排确认
        if skill and score >= _BM25_BORDERLINE_LOW:
            if on_progress:
                on_progress(f"🔍 BM25 模糊命中「{skill.name}」(score={score:.2f})，LLM 精排中...")

            # 取 Top-N 候选让 LLM 判断
            confirmed_skill = None
            confirmed_confidence = 0.0

            for cand_name, cand_score in candidates[:_LLM_CONFIRM_TOP_N]:
                cand_skill = next((s for s in skills if s.name == cand_name), None)
                if not cand_skill:
                    continue

                is_match, confidence = await _llm_confirm_match(user_input, cand_skill)
                if is_match and confidence > confirmed_confidence:
                    confirmed_skill = cand_skill
                    confirmed_confidence = confidence

            if confirmed_skill:
                if on_progress:
                    on_progress(f"✅ LLM 确认匹配「{confirmed_skill.name}」(置信度 {confirmed_confidence:.2f})")
                return RoutingResult(
                    complexity="medium",
                    matched_skill=confirmed_skill,
                    match_score=confirmed_confidence,
                    candidates=candidates,
                    action="execute_skill",
                )

            if on_progress:
                on_progress("❌ LLM 判定无匹配，走任务分解")

        # 低分 或 LLM 精排未确认
        # v3.0: 如果用户输入包含工具关键词，走 direct_tool 让 LLM 用 function calling 调工具
        # 而不是走 decompose 生成手动步骤
        if any(kw in user_input for kw in _TOOL_KEYWORDS):
            return RoutingResult(
                complexity="medium",
                match_score=score,
                candidates=candidates,
                action="direct_tool",
            )

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
