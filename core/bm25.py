"""
BM25 检索引擎 — 替代简单关键词重叠匹配

BM25 的核心优势：
- TF（词频）：词在文档中出现越多，相关性越高
- IDF（逆文档频率）：稀有词（如"PDF"、"研究"）比常见词（如"文件"）更重要
- 长度归一化：避免长文档天然得分高

这个模块为技能匹配提供 BM25 粗筛，替代原来粗糙的命中率 + Jaccard 公式。
"""
import math
import re
from typing import List, Tuple, Dict
from collections import Counter


# BM25 参数（经典值，一般不需要调）
_K1 = 1.5   # 词频饱和参数，越大越看重词频
_B = 0.75   # 长度归一化参数，0 = 不归一化，1 = 完全归一化


def _tokenize(text: str) -> List[str]:
    """中英文混合分词（jieba 优先，回退正则）"""
    try:
        import jieba
        return [w.strip().lower() for w in jieba.cut(text) if len(w.strip()) > 1]
    except ImportError:
        # 回退：中文按字拆，英文按词拆
        tokens = []
        for seg in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9_]+', text.lower()):
            if len(seg) > 1:
                tokens.append(seg)
        return tokens


class BM25Index:
    """
    BM25 索引。用法：

        index = BM25Index()
        index.add("skill-name-1", "桌面文件整理 整理 归类 文件 桌面")
        index.add("skill-name-2", "网络研究 搜索 调研 分析 AI 进展")
        index.build()
        results = index.search("找一下PDF文件", top_k=5)
    """

    def __init__(self):
        self._docs: Dict[str, List[str]] = {}       # doc_id → tokens
        self._doc_ids: List[str] = []
        self._df: Counter = Counter()                # 文档频率
        self._avg_dl: float = 0.0                    # 平均文档长度
        self._num_docs: int = 0
        self._built = False

    def add(self, doc_id: str, text: str):
        """添加文档（构建前调用）"""
        tokens = _tokenize(text)
        self._docs[doc_id] = tokens

    def build(self):
        """构建索引（计算 IDF 等统计量）"""
        self._doc_ids = list(self._docs.keys())
        self._num_docs = len(self._doc_ids)

        if self._num_docs == 0:
            self._built = True
            return

        # 计算文档频率
        self._df = Counter()
        total_len = 0
        for doc_id, tokens in self._docs.items():
            total_len += len(tokens)
            unique_tokens = set(tokens)
            for t in unique_tokens:
                self._df[t] += 1

        self._avg_dl = total_len / self._num_docs
        self._built = True

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        搜索最相关的文档。

        返回: [(doc_id, score), ...] 按分数降序，最多 top_k 个
        """
        if not self._built:
            self.build()

        if self._num_docs == 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = []
        for doc_id in self._doc_ids:
            score = self._score(query_tokens, doc_id)
            if score > 0:
                scores.append((doc_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _score(self, query_tokens: List[str], doc_id: str) -> float:
        """计算单个文档的 BM25 分数"""
        doc_tokens = self._docs[doc_id]
        dl = len(doc_tokens)
        doc_tf = Counter(doc_tokens)

        score = 0.0
        for qt in query_tokens:
            if qt not in doc_tf:
                continue

            tf = doc_tf[qt]
            df = self._df.get(qt, 0)

            # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            # 加 1 防止负 IDF（当词出现在所有文档中时）
            idf = math.log((self._num_docs - df + 0.5) / (df + 0.5) + 1)

            # TF 归一化
            tf_norm = (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * dl / self._avg_dl))

            score += idf * tf_norm

        return score

    def get_idf(self, token: str) -> float:
        """查看某个词的 IDF 值（调试用）"""
        df = self._df.get(token.lower(), 0)
        if df == 0:
            return 0.0
        return math.log((self._num_docs - df + 0.5) / (df + 0.5) + 1)
