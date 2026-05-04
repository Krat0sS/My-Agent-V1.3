#!/usr/bin/env python3
"""
模拟完整的路由流程，看"帮我整理桌面文件"到底走了哪条路。
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import tools.builtin_compat  # noqa: F401
from core.intent_router import route, classify_complexity, match_skill, _BM25_HIGH_CONFIDENCE, _BM25_BORDERLINE_LOW
from skills.loader import load_all_skills


async def main():
    test_inputs = [
        "帮我整理桌面文件",
        "整理桌面",
        "帮我整理一下桌面",
        "桌面太乱了帮我收拾一下",
        "organize my desktop files",
    ]

    skills = load_all_skills()
    print(f"已加载技能: {[s.name for s in skills]}")
    print(f"BM25 阈值: 高置信={_BM25_HIGH_CONFIDENCE}, 模糊下限={_BM25_BORDERLINE_LOW}")
    print()

    for user_input in test_inputs:
        print(f"{'='*60}")
        print(f"输入: {user_input}")

        # 1. 复杂度分类
        complexity = classify_complexity(user_input)
        print(f"  复杂度: {complexity}")

        # 2. BM25 匹配
        skill, score, candidates = match_skill(user_input, skills)
        print(f"  BM25 最佳: {skill.name if skill else 'None'} (score={score:.3f})")
        if candidates:
            print(f"  候选列表:")
            for name, s in candidates:
                marker = " ←" if skill and name == skill.name else ""
                print(f"    {name}: {s:.3f}{marker}")

        # 3. 完整路由
        routing = await route(user_input, skills)
        print(f"  路由结果: action={routing.action}, matched={routing.matched_skill.name if routing.matched_skill else None}")
        print(f"  match_score={routing.match_score:.3f}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
