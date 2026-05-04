"""RAG 知识库 — 文档导入、分块、向量化、语义检索

架构：
  文档 → 加载器 → 分块器 → 嵌入器 → 向量存储
  查询 → 嵌入器 → 向量检索 → Top-K 结果

支持的文档格式：.txt .md .py .js .json .csv .log .html .xml .css
可扩展：PDF（需 PyPDF2）、DOCX（需 python-docx）

嵌入策略（按优先级）：
  1. OpenAI 兼容 API（DeepSeek/OpenAI/etc）
  2. 本地 sentence-transformers（离线可用）
  3. TF-IDF 纯 Python 兜底（零依赖）

向量存储：
  - FAISS（推荐，高性能）
  - NumPy 兜底（零额外依赖）
"""

import os
import json
import hashlib
import datetime
import re
from typing import Optional

import config

# ═══ 配置 ═══
KB_DIR = os.path.join(config.WORKSPACE, "knowledge_base")
KB_INDEX_FILE = os.path.join(KB_DIR, "index.json")       # 文档元数据
KB_VECTORS_FILE = os.path.join(KB_DIR, "vectors.npy")     # 向量矩阵
KB_CHUNKS_FILE = os.path.join(KB_DIR, "chunks.json")      # 分块文本
KB_MANIFEST_FILE = os.path.join(KB_DIR, "manifest.json")  # 导入记录

# 分块参数
CHUNK_SIZE = 500       # 每块最大字符数
CHUNK_OVERLAP = 100    # 块间重叠字符数
MAX_CHUNKS_PER_QUERY = 5  # 每次检索返回的最大块数

# 嵌入维度（根据模型自动检测）
EMBEDDING_DIM = None

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".csv", ".tsv", ".log", ".html", ".htm",
    ".xml", ".css", ".scss", ".less", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".sh", ".bat",
    ".ps1", ".sql", ".r", ".java", ".c", ".cpp", ".h",
    ".hpp", ".go", ".rs", ".rb", ".php", ".swift",
    ".kt", ".scala", ".lua", ".pl", ".ex", ".exs",
}

# PDF/DOCX 需要额外依赖
try:
    import PyPDF2
    SUPPORTED_EXTENSIONS.add(".pdf")
except ImportError:
    pass

try:
    import docx
    SUPPORTED_EXTENSIONS.add(".docx")
except ImportError:
    pass


# ═══════════════════════════════════════════
# 1. 文本分块器
# ═══════════════════════════════════════════

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """将文本切分为重叠块

    策略：
    1. 优先按段落分割（双换行）
    2. 段落太长则按句子分割
    3. 句子太长则硬切
    4. 相邻块之间保留 overlap 字符的重叠
    """
    if not text or not text.strip():
        return []

    # 清理
    text = text.strip()

    # 如果文本足够短，直接返回
    if len(text) <= chunk_size:
        return [text]

    chunks = []

    # 第一步：按段落分割
    paragraphs = re.split(r'\n\s*\n', text)

    current_chunk = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 段落能放进当前块
        if len(current_chunk) + len(para) + 1 <= chunk_size:
            current_chunk = (current_chunk + "\n" + para).strip()
        else:
            # 保存当前块
            if current_chunk:
                chunks.append(current_chunk)

            # 段落本身太长，需要进一步分割
            if len(para) > chunk_size:
                sub_chunks = _split_long_text(para, chunk_size, overlap)
                chunks.extend(sub_chunks)
                current_chunk = ""
            else:
                # 新块开始，带重叠
                if chunks and overlap > 0:
                    tail = chunks[-1][-overlap:]
                    current_chunk = tail + "\n" + para
                    if len(current_chunk) > chunk_size:
                        current_chunk = para
                else:
                    current_chunk = para

    # 最后一块
    if current_chunk:
        chunks.append(current_chunk)

    return chunks

def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """对长文本进行硬切分，优先在标点/换行处断开"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:])
            break

        # 在 chunk_size 范围内找最佳断点
        best_break = end
        # 优先在换行处断
        newline_pos = text.rfind("\n", start, end)
        if newline_pos > start + chunk_size // 2:
            best_break = newline_pos + 1
        else:
            # 其次在句号/问号/感叹号处断
            for sep in ["。", "！", "？", ".", "!", "?", "；", ";", "，", ","]:
                pos = text.rfind(sep, start, end)
                if pos > start + chunk_size // 3:
                    best_break = pos + 1
                    break

        chunks.append(text[start:best_break])
        start = best_break - overlap if best_break - overlap > start else best_break

    return chunks


# ═══════════════════════════════════════════
# 2. 文档加载器
# ═══════════════════════════════════════════

def load_document(file_path: str) -> Optional[str]:
    """加载单个文档，返回纯文本内容"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return None

    try:
        if ext == ".pdf":
            return _load_pdf(file_path)
        elif ext == ".docx":
            return _load_docx(file_path)
        else:
            # 纯文本类文件
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception as e:
        print(f"[KB] 加载失败 {file_path}: {e}")
        return None

def _load_pdf(path: str) -> Optional[str]:
    """加载 PDF 文件"""
    try:
        import PyPDF2
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
    except Exception as e:
        print(f"[KB] PDF 加载失败: {e}")
        return None

def _load_docx(path: str) -> Optional[str]:
    """加载 DOCX 文件"""
    try:
        import docx
        doc = docx.Document(path)
        return "\n\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except Exception as e:
        print(f"[KB] DOCX 加载失败: {e}")
        return None

def scan_directory(dir_path: str, recursive: bool = True) -> list[str]:
    """扫描目录，返回所有支持的文件路径"""
    files = []
    if recursive:
        for root, dirs, filenames in os.walk(dir_path):
            # 跳过隐藏目录和常见无用目录
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                       {'node_modules', '__pycache__', '.git', 'venv', '.venv'}]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    files.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(dir_path):
            fp = os.path.join(dir_path, fn)
            if os.path.isfile(fp):
                ext = os.path.splitext(fn)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    files.append(fp)
    return sorted(files)


# ═══════════════════════════════════════════
# 3. 嵌入器（多后端自适应）
# ═══════════════════════════════════════════

class Embedder:
    """嵌入生成器，自动选择最佳可用后端"""

    def __init__(self):
        self.backend = None
        self.model = None
        self.dim = None
        self._init_backend()

    def _init_backend(self):
        """按优先级尝试初始化嵌入后端"""
        # 优先级 1: OpenAI 兼容 API
        if self._try_openai_api():
            return
        # 优先级 2: 本地 sentence-transformers
        if self._try_sentence_transformers():
            return
        # 优先级 3: TF-IDF 兜底
        self._init_tfidf()

    def _try_openai_api(self) -> bool:
        """尝试使用 OpenAI 兼容的嵌入 API"""
        api_key = config.LLM_API_KEY
        base_url = config.LLM_BASE_URL
        if not api_key:
            return False

        try:
            import httpx
            # 测试 API 是否支持嵌入
            # 使用 text-embedding-3-small 作为默认
            self.backend = "openai"
            self.model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
            self.dim = int(os.environ.get("EMBEDDING_DIM", "1536"))
            self._api_key = api_key
            self._base_url = base_url.rstrip("/")
            return True
        except ImportError:
            return False

    def _try_sentence_transformers(self) -> bool:
        """尝试使用本地 sentence-transformers"""
        try:
            from sentence_transformers import SentenceTransformer
            model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
            self.model = SentenceTransformer(model_name)
            self.backend = "local"
            self.dim = self.model.get_sentence_embedding_dimension()
            return True
        except (ImportError, Exception):
            return False

    def _init_tfidf(self):
        """TF-IDF 纯 Python 兜底"""
        self.backend = "tfidf"
        self.dim = 2048  # 固定维度
        self._vocab = {}
        self._idf = {}
        self._doc_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成嵌入向量"""
        if self.backend == "openai":
            return self._embed_openai(texts)
        elif self.backend == "local":
            return self._embed_local(texts)
        else:
            return self._embed_tfidf(texts)

    def embed_single(self, text: str) -> list[float]:
        """单文本嵌入"""
        return self.embed([text])[0]

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """通过 OpenAI 兼容 API 嵌入"""
        import httpx

        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        all_embeddings = []
        # 分批处理，每批最多 16 条
        batch_size = 16
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            payload = {
                "model": self.model,
                "input": batch,
                "encoding_format": "float"
            }
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                for item in sorted(data["data"], key=lambda x: x["index"]):
                    all_embeddings.append(item["embedding"])
            except Exception as e:
                print(f"[KB] OpenAI 嵌入 API 失败: {e}，回退到 TF-IDF")
                self._init_tfidf()
                return self._embed_tfidf(texts)

        return all_embeddings

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """本地 sentence-transformers 嵌入"""
        embeddings = self.model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def _embed_tfidf(self, texts: list[str]) -> list[list[float]]:
        """TF-IDF 嵌入（纯 Python，零依赖）"""
        import math

        # 分词（中英文混合）
        tokenized = [self._tokenize(t) for t in texts]

        # 更新词表和 IDF
        for tokens in tokenized:
            seen = set(tokens)
            for token in seen:
                self._idf[token] = self._idf.get(token, 0) + 1
            self._doc_count += 1

        # 构建向量
        embeddings = []
        for tokens in tokenized:
            vec = [0.0] * self.dim
            tf = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            for t, count in tf.items():
                # 哈希到固定维度
                idx = hash(t) % self.dim
                idf = math.log((self._doc_count + 1) / (self._idf.get(t, 1) + 1)) + 1
                val = (count / len(tokens)) * idf
                vec[idx] += val
            # L2 归一化
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            embeddings.append(vec)
        return embeddings

    def _tokenize(self, text: str) -> list[str]:
        """中英文混合分词"""
        try:
            import jieba
            return list(jieba.cut(text))
        except ImportError:
            # 简单分词：中文按字，英文按空格
            tokens = []
            buf = ""
            for ch in text:
                if '\u4e00' <= ch <= '\u9fff':
                    if buf:
                        tokens.extend(buf.lower().split())
                        buf = ""
                    tokens.append(ch)
                else:
                    buf += ch
            if buf:
                tokens.extend(buf.lower().split())
            return tokens


# ═══════════════════════════════════════════
# 4. 向量存储
# ═══════════════════════════════════════════

class VectorStore:
    """向量存储，支持 FAISS 和 NumPy 双后端"""

    def __init__(self, dim: int):
        self.dim = dim
        self.vectors = None      # numpy array (n, dim)
        self.chunks = []         # [{text, source, chunk_id, ...}]
        self.backend = None
        self._index = None
        self._init_backend()
        self._load()

    def _init_backend(self):
        """初始化向量存储后端"""
        try:
            import faiss
            self.backend = "faiss"
        except ImportError:
            self.backend = "numpy"

    def _load(self):
        """从磁盘加载已有数据"""
        if os.path.exists(KB_CHUNKS_FILE):
            try:
                with open(KB_CHUNKS_FILE, "r", encoding="utf-8") as f:
                    self.chunks = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.chunks = []

        if os.path.exists(KB_VECTORS_FILE):
            try:
                import numpy as np
                self.vectors = np.load(KB_VECTORS_FILE)
                if self.backend == "faiss" and self.vectors.shape[0] > 0:
                    import faiss
                    self._index = faiss.IndexFlatIP(self.dim)  # 内积（余弦相似度，需先归一化）
                    faiss.normalize_L2(self.vectors)
                    self._index.add(self.vectors)
            except Exception as e:
                print(f"[KB] 加载向量失败: {e}")
                self.vectors = None

    def save(self):
        """持久化到磁盘"""
        os.makedirs(KB_DIR, exist_ok=True)

        if self.vectors is not None:
            import numpy as np
            np.save(KB_VECTORS_FILE, self.vectors)

        with open(KB_CHUNKS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.chunks, f, ensure_ascii=False, indent=2)

        # 保存 manifest
        manifest = {
            "updated": datetime.datetime.now().isoformat(),
            "total_chunks": len(self.chunks),
            "dim": self.dim,
            "backend": self.backend,
        }
        sources = {}
        for c in self.chunks:
            src = c.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        manifest["sources"] = sources

        with open(KB_MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def add(self, embeddings: list[list[float]], chunks: list[dict]):
        """添加新的向量和分块"""
        import numpy as np

        new_vectors = np.array(embeddings, dtype=np.float32)

        if self.vectors is None:
            self.vectors = new_vectors
        else:
            self.vectors = np.vstack([self.vectors, new_vectors])

        self.chunks.extend(chunks)

        # 更新 FAISS 索引
        if self.backend == "faiss":
            import faiss
            if self._index is None:
                self._index = faiss.IndexFlatIP(self.dim)
            faiss.normalize_L2(new_vectors)
            self._index.add(new_vectors)

        self.save()

    def search(self, query_embedding: list[float], top_k: int = MAX_CHUNKS_PER_QUERY,
               min_score: float = 0.3) -> list[dict]:
        """语义搜索，返回 top_k 个最相关的分块"""
        import numpy as np

        if self.vectors is None or len(self.chunks) == 0:
            return []

        query = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query)

        if self.backend == "faiss" and self._index is not None:
            scores, indices = self._index.search(query, min(top_k, len(self.chunks)))
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(self.chunks):
                    continue
                if score < min_score:
                    continue
                result = {**self.chunks[idx], "score": float(score)}
                results.append(result)
            return results
        else:
            # NumPy 回退：余弦相似度
            faiss.normalize_L2(self.vectors)
            scores = np.dot(self.vectors, query.T).flatten()
            top_indices = np.argsort(scores)[::-1][:top_k]
            results = []
            for idx in top_indices:
                if scores[idx] < min_score:
                    continue
                result = {**self.chunks[int(idx)], "score": float(scores[idx])}
                results.append(result)
            return results

    def remove_source(self, source: str) -> int:
        """删除指定来源的所有分块，返回删除数量"""
        import numpy as np

        keep_indices = [i for i, c in enumerate(self.chunks) if c.get("source") != source]
        removed = len(self.chunks) - len(keep_indices)

        if removed == 0:
            return 0

        if keep_indices:
            self.chunks = [self.chunks[i] for i in keep_indices]
            self.vectors = self.vectors[keep_indices]

            # 重建 FAISS 索引
            if self.backend == "faiss":
                import faiss
                self._index = faiss.IndexFlatIP(self.dim)
                if len(self.vectors) > 0:
                    faiss.normalize_L2(self.vectors)
                    self._index.add(self.vectors)
        else:
            self.chunks = []
            self.vectors = None
            self._index = None

        self.save()
        return removed

    def clear(self):
        """清空所有数据"""
        self.chunks = []
        self.vectors = None
        self._index = None
        for f in [KB_VECTORS_FILE, KB_CHUNKS_FILE, KB_MANIFEST_FILE]:
            if os.path.exists(f):
                os.remove(f)

    def stats(self) -> dict:
        """返回知识库统计信息"""
        sources = {}
        for c in self.chunks:
            src = c.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        return {
            "total_chunks": len(self.chunks),
            "total_sources": len(sources),
            "dim": self.dim,
            "backend": self.backend,
            "sources": sources,
        }


# ═══════════════════════════════════════════
# 5. 知识库管理器（统一接口）
# ═══════════════════════════════════════════

class KnowledgeBase:
    """知识库统一管理接口"""

    def __init__(self):
        self.embedder = Embedder()
        self.store = VectorStore(self.embedder.dim)
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        if os.path.exists(KB_MANIFEST_FILE):
            try:
                with open(KB_MANIFEST_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"files": {}}

    def _save_manifest(self):
        os.makedirs(KB_DIR, exist_ok=True)
        with open(KB_MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, ensure_ascii=False, indent=2)

    def _file_hash(self, file_path: str) -> str:
        """计算文件内容哈希（用于去重/增量更新）"""
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(8192), b""):
                h.update(block)
        return h.hexdigest()

    def add_file(self, file_path: str) -> dict:
        """添加单个文件到知识库

        返回：{success, chunks_added, source, skipped}
        """
        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return {"success": False, "error": f"不支持的文件类型: {ext}"}

        # 检查是否已存在且未修改
        file_hash = self._file_hash(file_path)
        existing = self._manifest.get("files", {}).get(file_path)
        if existing and existing.get("hash") == file_hash:
            return {"success": True, "skipped": True, "reason": "文件未变化"}

        # 加载文档
        content = load_document(file_path)
        if not content:
            return {"success": False, "error": "无法读取文件内容"}

        # 分块
        chunks_text = chunk_text(content)
        if not chunks_text:
            return {"success": False, "error": "文件内容为空或无法分块"}

        # 如果之前导入过，先删除旧的
        if existing:
            self.store.remove_source(file_path)

        # 嵌入
        embeddings = self.embedder.embed(chunks_text)

        # 构建分块元数据
        chunk_metas = []
        for i, text in enumerate(chunks_text):
            chunk_metas.append({
                "text": text,
                "source": file_path,
                "filename": os.path.basename(file_path),
                "chunk_id": i,
                "total_chunks": len(chunks_text),
                "ext": ext,
                "added": datetime.datetime.now().isoformat(),
            })

        # 存储
        self.store.add(embeddings, chunk_metas)

        # 更新 manifest
        self._manifest.setdefault("files", {})[file_path] = {
            "hash": file_hash,
            "chunks": len(chunks_text),
            "added": datetime.datetime.now().isoformat(),
            "size": os.path.getsize(file_path),
        }
        self._save_manifest()

        return {
            "success": True,
            "chunks_added": len(chunks_text),
            "source": file_path,
            "filename": os.path.basename(file_path),
        }

    def add_directory(self, dir_path: str, recursive: bool = True) -> dict:
        """批量导入目录下的所有支持文件"""
        dir_path = os.path.abspath(dir_path)
        if not os.path.isdir(dir_path):
            return {"success": False, "error": f"目录不存在: {dir_path}"}

        files = scan_directory(dir_path, recursive)
        results = {"total": len(files), "added": 0, "skipped": 0, "failed": 0, "details": []}

        for fp in files:
            result = self.add_file(fp)
            if result.get("success"):
                if result.get("skipped"):
                    results["skipped"] += 1
                else:
                    results["added"] += 1
                    results["details"].append({
                        "file": os.path.basename(fp),
                        "chunks": result.get("chunks_added", 0)
                    })
            else:
                results["failed"] += 1
                results["details"].append({
                    "file": os.path.basename(fp),
                    "error": result.get("error")
                })

        return results

    def search(self, query: str, top_k: int = MAX_CHUNKS_PER_QUERY,
               min_score: float = 0.3) -> list[dict]:
        """语义搜索知识库"""
        query_embedding = self.embedder.embed_single(query)
        return self.store.search(query_embedding, top_k, min_score)

    def remove_file(self, file_path: str) -> dict:
        """从知识库中删除文件"""
        file_path = os.path.abspath(file_path)
        removed = self.store.remove_source(file_path)
        if file_path in self._manifest.get("files", {}):
            del self._manifest["files"][file_path]
            self._save_manifest()
        return {"success": True, "chunks_removed": removed}

    def clear(self):
        """清空知识库"""
        self.store.clear()
        self._manifest = {"files": {}}
        self._save_manifest()

    def stats(self) -> dict:
        """获取知识库统计"""
        store_stats = self.store.stats()
        return {
            **store_stats,
            "embedding_backend": self.embedder.backend,
            "embedding_model": self.embedder.model if self.embedder.backend != "tfidf" else "TF-IDF",
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        }


# ═══ 全局实例（延迟初始化） ═══
_kb_instance = None

def get_kb() -> KnowledgeBase:
    """获取知识库单例"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
    return _kb_instance
