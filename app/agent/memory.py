"""查询记忆（Query Memory）：Vanna 的核心思想。

每次成功执行的「问题→SQL」对自动沉淀到本地 JSON，
下次遇到相似问题时检索出最像的几条，作为 few-shot 注入提示词——
系统会随着使用越变越准。

相似度：优先向量检索（text-embedding-v4，支持换一种说法也能命中），
embedding 不可用时降级为字符 bigram Jaccard（纯 Python，零依赖）。
注意：百炼 embedding 接口单批上限 10 条，必须分批。
"""
import json
import math
import re
import time
from pathlib import Path

from app.config import BASE_DIR
from app.llm.client import get_client

DEFAULT_PATH = BASE_DIR / "data" / "query_memory.json"
MAX_ENTRIES = 500        # 记忆条目上限
TOP_K = 3
MIN_EMB_SCORE = 0.60     # 向量相似度阈值
MIN_BIGRAM_SCORE = 0.25  # bigram 降级阈值
EMBED_MODEL = "text-embedding-v4"
EMB_BATCH = 10           # 百炼 embedding 单批上限


def _bigrams(text: str) -> set[str]:
    """字符级 bigram，中文按字切，英文按字母对切。"""
    t = re.sub(r"\s+", "", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed(texts: list[str]) -> list[list[float]] | None:
    """批量向量化；失败返回 None（调用方降级）。"""
    try:
        client = get_client()
        out: list[list[float]] = []
        for i in range(0, len(texts), EMB_BATCH):  # 分批：单批上限 10
            resp = client.embeddings.create(model=EMBED_MODEL, input=texts[i : i + EMB_BATCH])
            out.extend(d.embedding for d in resp.data)
        return out
    except Exception:  # noqa: BLE001 —— 网络/额度问题不应阻断主流程
        return None


class QueryMemory:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self.entries: list[dict] = []
        self._emb_failed = False  # 本次进程内 embedding 是否已确认不可用
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.entries = json.loads(self.path.read_text(encoding="utf-8"))
                self._mtime = self.path.stat().st_mtime
            except (json.JSONDecodeError, OSError):
                self.entries = []
        else:
            self._mtime = 0.0

    def _reload_if_changed(self) -> None:
        """多进程安全：其它进程（如评测脚本）写入后自动重载。"""
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else 0.0
        except OSError:
            return
        if mtime != getattr(self, "_mtime", 0.0):
            self._load()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _backfill_embeddings(self) -> None:
        """为缺少向量的旧条目补算 embedding（首次升级后触发一次）。"""
        missing = [e for e in self.entries if not e.get("embedding")]
        if not missing:
            return
        vecs = _embed([e["question"] for e in missing])
        if vecs is None:
            self._emb_failed = True
            return
        for e, v in zip(missing, vecs):
            e["embedding"] = v
        self._save()

    def add(self, question: str, sql: str, source: str = "auto") -> bool:
        """记录一条问答对；同一问题（原文相同）只保留最新。"""
        q = question.strip()
        if not q or not sql.strip():
            return False
        entry = {
            "question": q,
            "sql": sql.strip(),
            "source": source,
            "ts": int(time.time()),
        }
        vecs = _embed([q]) if not self._emb_failed else None
        if vecs:
            entry["embedding"] = vecs[0]
        self.entries = [e for e in self.entries if e["question"] != q]
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]
        self._save()
        return True

    def search(self, question: str, k: int = TOP_K) -> list[dict]:
        """检索最相似的历史问答对（向量优先，bigram 兜底）。"""
        self._reload_if_changed()
        if not self.entries:
            return []
        self._backfill_embeddings()

        q_emb = None
        if not self._emb_failed:
            vecs = _embed([question])
            if vecs:
                q_emb = vecs[0]
            else:
                self._emb_failed = True

        q_gram = _bigrams(question)
        scored = []
        for e in self.entries:
            if q_emb and e.get("embedding"):
                score = _cosine(q_emb, e["embedding"])
                if score >= MIN_EMB_SCORE:
                    scored.append((score, e))
            else:
                score = _jaccard(q_gram, _bigrams(e["question"]))
                if score >= MIN_BIGRAM_SCORE:
                    scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:k]]

    def render_for_prompt(self, question: str) -> str:
        """渲染成可注入提示词的 few-shot 文本；无相似条目则返回空串。"""
        hits = self.search(question)
        if not hits:
            return ""
        lines = ["以下是历史相似问题及其验证过的 SQL，写法可参考："]
        for e in hits:
            lines.append(f"历史问题: {e['question']}\n历史SQL: {e['sql']}")
        return "\n\n".join(lines)

    def clear(self) -> int:
        n = len(self.entries)
        self.entries = []
        self._save()
        return n


_memory: QueryMemory | None = None


def get_memory() -> QueryMemory:
    global _memory
    if _memory is None:
        _memory = QueryMemory()
    return _memory
