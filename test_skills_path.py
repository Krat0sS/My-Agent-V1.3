#!/usr/bin/env python3
"""诊断技能加载问题"""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import tools.builtin_compat

print(f"config.WORKSPACE = {config.WORKSPACE}")
print()

# 检查各种可能的 skills 路径
candidates = [
    Path(os.path.join(config.WORKSPACE, "skills")),
    Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")),
    Path("skills"),
    Path("./skills"),
]

for p in candidates:
    exists = p.exists()
    print(f"  {p.resolve()} → {'✅ 存在' if exists else '❌ 不存在'}")
    if exists:
        for d in sorted(p.iterdir()):
            if d.is_dir():
                skill_md = d / "SKILL.md"
                print(f"    📁 {d.name}/ → SKILL.md {'✅' if skill_md.exists() else '❌'}")

print()
from skills.loader import load_all_skills
skills = load_all_skills()
print(f"load_all_skills() 返回: {len(skills)} 个技能")
for s in skills:
    print(f"  - {s.name}: {s.goal[:50]}")
