"""结果加工：图表类型判定（启发式）+ 自然语言总结（LLM）。"""
import json
import re
from datetime import date, datetime
from decimal import Decimal

from app.llm.client import chat

# ---------- 图表判定（零成本启发式，不依赖 LLM） ----------

_DATE_HINTS = re.compile(r"(日期|月份|日|月|年|date|month|year|季度)", re.IGNORECASE)
_RATIO_HINTS = re.compile(r"(占比|比例|份额|构成|分布百分比)")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def suggest_chart(columns: list[str], rows: list[list]) -> str | None:
    """返回 'bar' | 'line' | 'pie' | None。"""
    if not rows or len(columns) < 2:
        return None
    numeric_cols = [i for i, v in enumerate(rows[0]) if _is_number(v)]
    if len(numeric_cols) != 1 or numeric_cols[0] != len(columns) - 1:
        # 只处理"一个标签列 + 末尾一个数值列"的简单形态
        return None
    label_col, value_col = columns[0], columns[-1]

    if _RATIO_HINTS.search(label_col + " " + value_col) and len(rows) <= 8:
        return "pie"
    if _DATE_HINTS.search(label_col):
        return "line"
    return "bar"


# ---------- 自然语言总结 ----------

def _fmt_cell(v):
    if isinstance(v, Decimal):
        return round(float(v), 4)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (date, datetime)):
        return str(v)
    return v


def summarize(question: str, sql: str, columns: list[str], rows: list[list]) -> dict:
    """让 LLM 基于查询结果回答用户问题；解析失败时降级为模板回答。"""
    if not rows:
        return {
            "answer": "查询没有返回数据：可能是当前数据库里没有满足条件的数据，或筛选条件过严。",
            "followups": [],
        }

    # 只把前 20 行喂给 LLM，防止上下文爆炸
    sample = [ [_fmt_cell(c) for c in row] for row in rows[:20] ]
    system = (
        "你是数据分析助手。根据用户问题、SQL 与查询结果，用简洁的中文直接回答问题。"
        "要求：1) 直接给结论和关键数字，不要复述 SQL；2) 数字保留合理精度；"
        "3) 如果只是结果节选要说明。同时给出 2 个用户可能想追问的问题。"
        "只输出 JSON：{\"answer\": \"...\", \"followups\": [\"...\", \"...\"]}"
    )
    user = (
        f"用户问题: {question}\n\n执行的 SQL:\n{sql}\n\n"
        f"结果列: {columns}\n结果(前{len(sample)}行/共{len(rows)}行): {json.dumps(sample, ensure_ascii=False)}"
    )
    raw = chat(system, user, temperature=0.2).strip()
    try:
        data = json.loads(strip_json_fence(raw))
        return {
            "answer": str(data.get("answer", "")).strip(),
            "followups": [str(x) for x in data.get("followups", [])][:2],
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {
            "answer": f"查询完成，共 {len(rows)} 行结果，请查看下方明细表格。",
            "followups": [],
        }


def strip_json_fence(text: str) -> str:
    """去掉 JSON 外面的 ```json 围栏。"""
    t = text.strip()
    if t.startswith("```"):
        first = t.find("\n")
        if first != -1:
            t = t[first + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    m = re.search(r"\{.*\}", t, re.DOTALL)
    return m.group(0) if m else t
