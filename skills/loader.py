"""
技能加载器 — 扫描 skills/ 目录，解析 SKILL.md

每个技能是一个目录：
  skills/desktop-organize/
  ├── SKILL.md       # 技能描述（给 Agent 看的）
  └── tools.py       # 可选：工具实现（自动注册到 registry）
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict
import config


# 同义词扩展组 — 提高技能匹配率
_SYNONYMS = [
    {"整理", "清理", "归类", "归档", "收拾", "乱", "organize", "clean", "sort"},
    {"桌面", "desktop", "屏幕"},
    {"文件", "文档", "file", "files", "document"},
    {"搜索", "查找", "寻找", "找", "搜", "查询", "search", "find"},
    {"研究", "调研", "分析", "进展", "最新", "research", "analyze"},
    {"网络", "网页", "互联网", "AI", "科技", "web", "internet"},
    {"报告", "简报", "总结", "report", "summary"},
    {"分类", "归类", "归类", "classify", "categorize"},
    {"移动", "转移", "搬", "move"},
    {"扫描", "scan", "浏览"},
    {"下载", "download"},
    {"打开", "启动", "open", "launch", "start"},
    {"截图", "截屏", "screenshot"},
    {"浏览", "browser", "浏览"},
    {"PDF", "pdf", "Word", "word", "Excel", "excel", "PPT", "ppt", "文档格式"},
    {"AI", "人工智能", "大模型", "LLM", "GPT", "ChatGPT", "深度学习", "机器学习"},
    {"代码", "编程", "程序", "code", "programming", "开发"},
]


@dataclass
class Skill:
    """一个已加载的技能"""
    name: str                    # 目录名，如 "desktop-organize"
    path: str                    # 技能目录的绝对路径
    goal: str = ""               # ## 目标 的内容
    tools: List[str] = field(default_factory=list)  ## 前置工具
    steps: List[str] = field(default_factory=list)   ## 执行步骤
    pitfalls: List[str] = field(default_factory=list)  ## 陷阱与检查点
    raw_md: str = ""             # 原始 SKILL.md 内容
    keywords: List[str] = field(default_factory=list)  # 提取的关键词


def parse_skill_md(content: str) -> dict:
    """
    解析 SKILL.md 的结构化内容。
    按 ## 标题切分，提取各段落。
    """
    sections = {}
    current_section = None
    current_lines = []

    for line in content.split("\n"):
        # 检测 ## 标题
        match = re.match(r'^##\s+(.+)', line)
        if match:
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return sections


def _extract_keywords(text: str) -> List[str]:
    """从技能描述中提取关键词（用于匹配），使用 jieba 中文分词"""
    try:
        import jieba
        words = list(jieba.cut(text.lower()))
    except ImportError:
        # jieba 不可用时回退到正则
        words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9_]+', text.lower())
    # 过滤停用词、空白和过短的词
    stopwords = {'的', '了', '是', '在', '有', '和', '与', '或', '等', '被', '把',
                 '从', '到', '对', '中', '上', '下', '不', '也', '都', '就', '还',
                 'the', 'a', 'an', 'is', 'are', 'and', 'or', 'to', 'of', 'in',
                 '一个', '可以', '用于', '通过', '进行', '以及', '或者', '然后'}
    return [w.strip() for w in words if len(w.strip()) > 1 and w.strip() not in stopwords]


def _extract_tools_list(text: str) -> List[str]:
    """从前置工具段落中提取工具名列表"""
    tools = []
    for line in text.split("\n"):
        line = line.strip().lstrip("- •*")
        # 匹配工具名（英文标识符格式）
        match = re.match(r'^[a-z_][a-z0-9_]*', line)
        if match:
            tools.append(match.group())
        # 也支持行内 code 格式
        for code in re.findall(r'`([a-z_][a-z0-9_]*)`', line):
            if code not in tools:
                tools.append(code)
    return tools


def load_skill(skill_dir: Path) -> Optional[Skill]:
    """加载单个技能目录"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    content = skill_md.read_text(encoding="utf-8")
    sections = parse_skill_md(content)

    # 提取目标（支持"目标"或"Goal"标题）
    goal = sections.get("目标", sections.get("Goal", ""))

    # 提取前置工具
    tools_text = sections.get("前置工具", sections.get("Required Tools", ""))
    tools = _extract_tools_list(tools_text)

    # 提取执行步骤
    steps_text = sections.get("执行步骤", sections.get("Steps", ""))
    steps = [line.strip().lstrip("0123456789. ")
             for line in steps_text.split("\n")
             if line.strip() and not line.strip().startswith("#")]

    # 提取陷阱
    pitfalls_text = sections.get("陷阱与检查点", sections.get("Pitfalls", ""))
    pitfalls = [line.strip().lstrip("- •*")
                for line in pitfalls_text.split("\n")
                if line.strip() and not line.strip().startswith("#")]

    # 提取关键词（从目标 + 文件名 + 同义词扩展）
    keywords = _extract_keywords(goal + " " + skill_dir.name)
    # 将技能名拆分（desktop-organize → desktop, organize）
    for part in skill_dir.name.replace("-", " ").replace("_", " ").split():
        if len(part) > 1 and part not in keywords:
            keywords.append(part)
    # 添加常见同义词扩展，提高匹配率
    for kw in list(keywords):
        for syn_group in _SYNONYMS:
            if kw in syn_group:
                for syn in syn_group:
                    if syn not in keywords:
                        keywords.append(syn)
    # 也从 SKILL.md 的 # 标题行提取关键词
    for line in content.split("\n"):
        if line.startswith("# ") and not line.startswith("## "):
            keywords.extend(_extract_keywords(line))
            break

    # 加载可选的 tools.py（如果有的话，自动注册到 registry）
    tools_py = skill_dir / "tools.py"
    if tools_py.exists():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"skills.{skill_dir.name}.tools", tools_py
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"⚠️ 加载技能工具 {skill_dir.name}/tools.py 失败: {e}")

    return Skill(
        name=skill_dir.name,
        path=str(skill_dir),
        goal=goal,
        tools=tools,
        steps=steps,
        pitfalls=pitfalls,
        raw_md=content,
        keywords=keywords,
    )


def load_all_skills(skills_dir: Path = None) -> List[Skill]:
    """加载所有技能（合并 workspace 技能 + 项目内置技能）"""
    skill_dirs = []

    if skills_dir is not None:
        skill_dirs.append(skills_dir)
    else:
        # 1. workspace 技能（用户自建 + 万物沉淀）
        ws_skills = Path(os.path.join(config.WORKSPACE, "skills"))
        if ws_skills.exists():
            skill_dirs.append(ws_skills)

        # 2. 项目内置技能（desktop-organize, file-search, web-research）
        builtin_skills = Path(os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills"))
        if builtin_skills.exists() and builtin_skills != ws_skills:
            skill_dirs.append(builtin_skills)

    skills = []
    seen_names = set()

    for skills_dir in skill_dirs:
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".") and d.name != "__pycache__":
                if d.name in seen_names:
                    continue
                try:
                    skill = load_skill(d)
                    if skill:
                        skills.append(skill)
                        seen_names.add(d.name)
                except Exception as e:
                    print(f"⚠️ 加载技能 {d.name} 失败: {e}")

    return skills


def get_skill_prompt_context(skills: List[Skill] = None) -> str:
    """
    收集所有技能的摘要，注入到 System Prompt。
    不是注入完整的 SKILL.md（太长），而是注入目标和关键词。
    """
    if skills is None:
        skills = load_all_skills()

    if not skills:
        return ""

    contexts = ["## 已掌握的技能\n"]
    for skill in skills:
        tools_str = ", ".join(skill.tools) if skill.tools else "无特殊要求"
        steps_preview = f"{len(skill.steps)} 步" if skill.steps else "自动"
        contexts.append(
            f"- **{skill.name}**：{skill.goal[:100]}\n"
            f"  工具：{tools_str} | 步骤：{steps_preview}"
        )

    return "\n".join(contexts)
