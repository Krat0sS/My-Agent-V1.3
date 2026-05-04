"""测试 BM25 技能匹配是否修复了原来的两个失败 case"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from core.bm25 import BM25Index
from skills.loader import load_all_skills, Skill
from pathlib import Path


def test_bm25_raw():
    """直接测试 BM25 引擎"""
    print("=" * 60)
    print("🧪 测试 1: BM25 引擎原始测试")
    print("=" * 60)

    index = BM25Index()
    index.add("file-search", "文件搜索 查找 文件 文档 类型 日期 大小 搜索 find files document search scan")
    index.add("web-research", "网络研究 搜索 调研 分析 AI 进展 最新 research analyze web internet 科技 报告 总结")
    index.add("desktop-organize", "桌面文件整理 整理 清理 归类 归档 收拾 桌面 文件 organize clean sort desktop")
    index.build()

    test_cases = [
        ("找一下PDF文件", "file-search"),
        ("帮我研究AI最新进展", "web-research"),
        ("整理桌面", "desktop-organize"),
        ("打开百度", None),  # 不应该匹配任何技能
    ]

    for query, expected in test_cases:
        results = index.search(query, top_k=3)
        print(f"\n📝 输入: {query}")
        print(f"   期望: {expected}")
        print(f"   BM25 结果:")
        for name, score in results:
            marker = " ✅" if name == expected else ""
            print(f"     {name}: {score:.3f}{marker}")

        if results:
            best_name = results[0][0]
            if best_name == expected:
                print(f"   ✅ 命中!")
            else:
                print(f"   ❌ 未命中 (匹配到了 {best_name})")
        else:
            if expected is None:
                print(f"   ✅ 正确未匹配")
            else:
                print(f"   ❌ 未命中 (无结果)")


def test_with_real_skills():
    """用真实技能文件测试"""
    print("\n" + "=" * 60)
    print("🧪 测试 2: 真实技能匹配")
    print("=" * 60)

    skills_dir = Path(os.path.join(os.path.dirname(__file__), "skills"))
    skills = load_all_skills(skills_dir)

    if not skills:
        print("❌ 没有加载到技能")
        return

    print(f"已加载 {len(skills)} 个技能:")
    for s in skills:
        print(f"  - {s.name}: {s.goal[:50]}...")

    # 构建 BM25 索引
    index = BM25Index()
    for skill in skills:
        doc_text = f"{skill.goal} {' '.join(skill.keywords)} {skill.name.replace('-', ' ')}"
        index.add(skill.name, doc_text)
    index.build()

    test_cases = [
        ("找一下PDF文件", "file-search"),
        ("帮我研究AI最新进展", "web-research"),
        ("整理桌面文件", "desktop-organize"),
        ("帮我搜索文档", "file-search"),
        ("搜索AI最新研究", "web-research"),
    ]

    print("\n匹配测试:")
    for query, expected in test_cases:
        results = index.search(query, top_k=3)
        print(f"\n📝 输入: {query}")
        print(f"   期望: {expected}")
        for name, score in results:
            marker = " ✅" if name == expected else ""
            print(f"     {name}: {score:.3f}{marker}")

        if results and results[0][0] == expected:
            print(f"   ✅ 命中!")
        else:
            print(f"   ❌ 未命中")


if __name__ == "__main__":
    test_bm25_raw()
    test_with_real_skills()
