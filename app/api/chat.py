"""HTTP 接口层：/api/chat、/api/schema、/api/health。"""
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent.memory import get_memory
from app.agent.pipeline import NL2SQLAgent
from app.db.inspector import get_inspector

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[dict] = Field(default_factory=list, max_length=5)


@lru_cache
def _agent() -> NL2SQLAgent:
    return NL2SQLAgent()


@router.post("/chat")
def chat(req: ChatRequest) -> dict:
    return _agent().answer(req.question, req.history)


@router.get("/schema")
def schema() -> dict:
    insp = get_inspector()
    return {"tables": insp.get_table_names(), "schema": insp.full_schema()}


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "model": _agent is not None}


@router.get("/memory")
def memory_list() -> dict:
    """查询记忆列表（已沉淀的问题→SQL 对）。"""
    return {"count": len(get_memory().entries), "entries": get_memory().entries}


@router.delete("/memory")
def memory_clear() -> dict:
    return {"cleared": get_memory().clear()}
