"""表路由：大库时先让 LLM 挑出与问题相关的表，避免全量 schema 撑爆上下文。"""
import json
import re

from app.llm.client import chat, strip_code_fence

MAX_TABLES_FOR_FULL_SCHEMA = 8


def route_tables(schema_summary: str, question: str, history: str = "") -> list[str]:
    """返回与问题相关的表名列表。表数量少时直接返回全部（省一次 LLM 调用）。"""
    all_tables = [
        line.split(":")[0].lstrip("- ").strip()
        for line in schema_summary.splitlines() if line.strip()
    ]
    if len(all_tables) <= MAX_TABLES_FOR_FULL_SCHEMA:
        return all_tables

    system = (
        "你是数据库助手。根据用户问题，从候选表清单中挑出回答该问题所必需的表。"
        "只输出 JSON 数组，如 [\"orders\", \"customers\"]，不要解释。"
    )
    user = f"候选表清单:\n{schema_summary}\n\n对话历史:\n{history}\n\n用户问题: {question}"
    raw = strip_code_fence(chat(system, user))
    try:
        tables = json.loads(raw)
        if isinstance(tables, list):
            valid = [t for t in tables if t in all_tables]
            return valid or all_tables
    except (json.JSONDecodeError, TypeError):
        pass
    # 兜底：从回复里抠出合法表名
    found = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", raw)
    matched = [t for t in all_tables if t in found]
    return matched or all_tables
