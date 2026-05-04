"""独立 BM25 测试 — 不依赖项目其他模块"""
import math
import re
from typing import List, Tuple, Dict
from collections import Counter

_K1 = 1.5
_B = 0.75

# 同义词扩展组（从 loader.py 搬过来的）
_SYNONYMS = [
    {"整理", "清理", "归类", "归档", "收拾", "乱", "organize", "clean", "sort"},
    {"桌面", "desktop", "屏幕"},
    {"文件", "文档", "file", "files", "document"},
    {"搜索", "查找", "寻找", "找", "搜", "查询", "search", "find"},
    {"研究", "调研", "分析", "进展", "最新", "research", "analyze"},
    {"网络", "网页", "互联网", "AI", "科技", "web", "internet"},
    {"报告", "简报", "总结", "report", "summary"},
    {"分类", "归类", "classify", "categorize"},
    {"移动", "转移", "搬", "move"},
    {"扫描", "scan", "浏览"},
    {"下载", "download"},
    {"打开", "启动", "open", "launch", "start"},
    {"截图", "截屏", "screenshot"},
]

_STOPWORDS = {'的', '了', '是', '在', '有', '和', '与', '或', '等', '被', '把',
              '从', '到', '对', '中', '上', '下', '不', '也', '都', '就', '还',
              'the', 'a', 'an', 'is', 'are', 'and', 'or', 'to', 'of', 'in',
              '一个', '可以', '用于', '通过', '进行', '以及', '或者', '然后', '一下', '帮我'}


def tokenize(text: str) -> List[str]:
    """中英文混合分词（jieba 优先）"""
    try:
        import jieba
        tokens = [w.strip().lower() for w in jieba.cut(text) if len(w.strip()) > 1 and w.strip().lower() not in _STOPWORDS]
        return tokens
    except ImportError:
        tokens = []
        for seg in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9_]+', text.lower()):
            if len(seg) > 1 and seg not in _STOPWORDS:
                tokens.append(seg)
        return tokens


def expand_synonyms(keywords: List[str]) -> List[str]:
    """同义词扩展"""
    expanded = list(keywords)
    for kw in keywords:
        for syn_group in _SYNONYMS:
            if kw in syn_group:
                for syn in syn_group:
                    if syn not in expanded:
                        expanded.append(syn)
    return expanded


class BM25Index:
    def __init__(self):
        self._docs: Dict[str, List[str]] = {}
        self._doc_ids: List[str] = []
        self._df: Counter = Counter()
        self._avg_dl: float = 0.0
        self._num_docs: int = 0

    def add(self, doc_id: str, tokens: List[str]):
        self._docs[doc_id] = tokens

    def build(self):
        self._doc_ids = list(self._docs.keys())
        self._num_docs = len(self._doc_ids)
        self._df = Counter()
        total_len = 0
        for doc_id, tokens in self._docs.items():
            total_len += len(tokens)
            for t in set(tokens):
                self._df[t] += 1
        self._avg_dl = total_len / max(self._num_docs, 1)

    def search(self, query_tokens: List[str], top_k: int = 5) -> List[Tuple[str, float]]:
        if self._num_docs == 0:
            return []
        scores = []
        for doc_id in self._doc_ids:
            score = self._score(query_tokens, doc_id)
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _score(self, query_tokens: List[str], doc_id: str) -> float:
        doc_tokens = self._docs[doc_id]
        dl = len(doc_tokens)
        doc_tf = Counter(doc_tokens)
        score = 0.0
        for qt in query_tokens:
            if qt not in doc_tf:
                continue
            tf = doc_tf[qt]
            df = self._df.get(qt, 0)
            idf = math.log((self._num_docs - df + 0.5) / (df + 0.5) + 1)
            tf_norm = (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * dl / max(self._avg_dl, 1)))
            score += idf * tf_norm
        return score

    def get_idf(self, token: str) -> float:
        df = self._df.get(token.lower(), 0)
        if df == 0:
            return 0.0
        return math.log((self._num_docs - df + 0.5) / (df + 0.5) + 1)


# ═══ 模拟技能数据 ═══

SKILLS = {
    "file-search": {
        "goal": "根据用户描述的条件搜索文件，返回按相关度排序的结果列表。支持按文件扩展名如 PDF、Word、Excel、图片、视频筛选",
        "keywords": ["文件", "搜索", "查找", "寻找", "找", "搜", "查询", "search", "find", "files", "document", "扫描", "scan", "PDF", "pdf", "Word", "Excel"],
    },
    "web-research": {
        "goal": "针对一个主题进行网络搜索、信息收集和整理，输出结构化的研究摘要。适用于技术调研、AI 进展追踪、行业分析",
        "keywords": ["网络", "研究", "搜索", "调研", "分析", "AI", "进展", "最新", "research", "analyze", "web", "internet", "科技", "报告", "总结", "人工智能", "大模型", "LLM"],
    },
    "desktop-organize": {
        "goal": "一键扫描指定目录，按文件类型自动分类并移动到对应文件夹",
        "keywords": ["桌面", "文件", "整理", "清理", "归类", "归档", "收拾", "organize", "clean", "sort", "desktop", "分类"],
    },
}


def test_old_method():
    """旧方法（关键词重叠 + Jaccard）"""
    print("=" * 60)
    print("📊 旧方法: 关键词重叠 + Jaccard")
    print("=" * 60)

    test_cases = [
        ("找一下PDF文件", "file-search"),
        ("帮我研究AI最新进展", "web-research"),
        ("整理桌面", "desktop-organize"),
        ("搜索文档", "file-search"),
    ]

    for query, expected in test_cases:
        user_kw = set(tokenize(query))
        print(f"\n📝 输入: {query}")
        print(f"   用户关键词: {user_kw}")

        best_score = 0
        best_name = None
        for name, skill in SKILLS.items():
            skill_kw = set(expand_synonyms(skill["keywords"]))
            overlap = user_kw & skill_kw
            hit_rate = len(overlap) / len(user_kw) if user_kw else 0
            jaccard = len(overlap) / len(user_kw | skill_kw)
            score = hit_rate * 0.7 + jaccard * 0.3
            print(f"   {name}: overlap={overlap}, hit_rate={hit_rate:.2f}, jaccard={jaccard:.2f}, score={score:.3f}")
            if score > best_score:
                best_score = score
                best_name = name

        threshold = 0.4
        if best_name == expected and best_score >= threshold:
            print(f"   ✅ 命中 {best_name} (score={best_score:.3f} >= {threshold})")
        elif best_name:
            print(f"   ❌ 未命中 (匹配到 {best_name}, score={best_score:.3f}, 阈值={threshold})")
        else:
            print(f"   ❌ 未命中 (无结果)")


def test_new_bm25():
    """新方法（BM25）"""
    print("\n" + "=" * 60)
    print("📊 新方法: BM25")
    print("=" * 60)

    # 构建索引
    index = BM25Index()
    for name, skill in SKILLS.items():
        tokens = tokenize(skill["goal"]) + expand_synonyms(skill["keywords"])
        index.add(name, tokens)
    index.build()

    # 打印 IDF 值
    print("\n📊 关键词 IDF 值:")
    idf_words = ["PDF", "文件", "研究", "搜索", "AI", "整理", "桌面", "找", "最新", "进展"]
    for w in idf_words:
        idf = index.get_idf(w)
        print(f"   {w}: {idf:.3f}")

    test_cases = [
        ("找一下PDF文件", "file-search"),
        ("帮我研究AI最新进展", "web-research"),
        ("整理桌面", "desktop-organize"),
        ("搜索文档", "file-search"),
        ("帮我搜索AI最新研究", "web-research"),
    ]

    high_threshold = 2.0
    low_threshold = 0.5

    for query, expected in test_cases:
        user_tokens = tokenize(query)
        results = index.search(user_tokens, top_k=3)
        print(f"\n📝 输入: {query}")
        print(f"   用户 tokens: {user_tokens}")
        print(f"   BM25 结果:")
        for name, score in results:
            marker = " ✅" if name == expected else ""
            zone = "🟢直接命中" if score >= high_threshold else ("🟡LLM精排" if score >= low_threshold else "🔴不匹配")
            print(f"     {name}: {score:.3f} {zone}{marker}")

        if results and results[0][0] == expected:
            s = results[0][1]
            if s >= high_threshold:
                print(f"   ✅ 高置信命中! (score={s:.3f} >= {high_threshold})")
            elif s >= low_threshold:
                print(f"   🟡 BM25 模糊命中 (score={s:.3f})，需 LLM 精排确认")
            else:
                print(f"   ❌ 分数太低 ({s:.3f} < {low_threshold})")
        else:
            print(f"   ❌ 未命中")


if __name__ == "__main__":
    test_old_method()
    test_new_bm25()
