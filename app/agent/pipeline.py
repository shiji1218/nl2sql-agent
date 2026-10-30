"""Pipeline 编排：路由 → 生成 → 护栏 → 执行 → （报错则自纠错重试）→ 总结。"""
import time
from decimal import Decimal
from datetime import date, datetime

from sqlalchemy import create_engine, text

from app.llm.client import chat

from app.agent.answer import suggest_chart, summarize
from app.agent.decomposer import decompose
from app.agent.generator import generate_sql
from app.agent.guard import SQLGuardError, check_sql
from app.agent.memory import get_memory
from app.agent.router import route_tables
from app.config import settings
from app.db.inspector import get_inspector


def _fmt_cell(v):
    if isinstance(v, Decimal):
        return round(float(v), 4)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (date, datetime)):
        return str(v)
    return v


class NL2SQLAgent:
    def __init__(self) -> None:
        self.inspector = get_inspector()
        self.engine = create_engine(settings.database_url)
        self.memory = get_memory()

    def _execute(self, sql: str) -> tuple[list[str], list[list]]:
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [[_fmt_cell(c) for c in row] for row in result.fetchmany(settings.max_rows)]
        return columns, rows

    def answer(
        self,
        question: str,
        history: list[dict] | None = None,
    ) -> dict:
        """完整回答一次自然语言查询（复合问题自动拆分）。

        history: [{"question","sql"}] 最近几轮，用于多轮指代。
        """
        t0 = time.time()
        history_text = "\n".join(
            f"问: {h.get('question','')}\nSQL: {h.get('sql','')}"
            for h in (history or [])[-3:]
        )

        # 0. 查询分解：复合问题拆成 ≤3 个子问题分别执行
        sub_questions = decompose(question, self.inspector.schema_summary())

        if len(sub_questions) == 1:
            result = self._answer_single(question, history_text)
            result["compound"] = False
            return result

        sub_results = [self._answer_single(q, history_text) for q in sub_questions]
        answer_text = self._synthesize(question, sub_results)
        return {
            "success": any(r.get("success") for r in sub_results),
            "question": question,
            "compound": True,
            "sub_questions": sub_questions,
            "sub_results": sub_results,
            "answer": answer_text,
            "followups": next(
                (r.get("followups") or [] for r in sub_results if r.get("followups")), []
            ),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def _synthesize(self, question: str, sub_results: list[dict]) -> str:
        """把各子问题的结论整合成一段总结。"""
        parts = []
        for i, r in enumerate(sub_results, 1):
            if r.get("success"):
                parts.append(f"子问题{i}「{r['question']}」的结论：{r['answer']}")
            else:
                parts.append(f"子问题{i}「{r['question']}」查询失败：{r.get('error', '')[:100]}")
        fallback = "；".join(p.split("：", 1)[1] for p in parts)
        try:
            system = (
                "你是数据分析助手。用户的复合问题已被拆分成多个子问题并分别查询，"
                "请把各子问题的结论整合成一段简洁的中文总结：直接给结论和关键数字，"
                "用序号分点，不要复述查询过程。"
            )
            user = f"用户原始问题: {question}\n\n" + "\n\n".join(parts)
            return chat(system, user, temperature=0.2).strip() or fallback
        except Exception:  # noqa: BLE001 —— 汇总失败时降级为拼接
            return fallback

    def _answer_single(self, question: str, history_text: str = "") -> dict:
        """单一问题的完整链路：路由 → 生成 → 护栏 → 执行 → 自纠错 → 总结。"""
        t0 = time.time()

        # 1. 表路由
        tables = route_tables(self.inspector.schema_summary(), question, history_text)
        # 2. 取相关表的 DDL（小库等价于全量），并附加业务指标口径（迷你语义层）
        schema_ddl = "\n\n".join(self.inspector.get_table_ddl(t) for t in tables)
        metrics = self.inspector.metrics_text()
        if metrics:
            schema_ddl += f"\n\n{metrics}"
        # 查询记忆：检索相似历史问答对作为 few-shot（越用越准的关键）
        examples = self.memory.render_for_prompt(question)

        # 3. 生成 → 护栏 → 执行，报错进入自纠错循环
        sql, error = "", ""
        attempts = 0
        columns: list[str] = []
        rows: list[list] = []
        while attempts <= settings.max_repair_attempts:
            attempts += 1
            raw_sql = generate_sql(schema_ddl, question, history_text, sql, error, examples)
            try:
                sql = check_sql(raw_sql)
                columns, rows = self._execute(sql)
                error = ""
                break
            except SQLGuardError as e:
                error = f"安全检查未通过: {e}"
                sql = raw_sql
            except Exception as e:  # noqa: BLE001 —— 数据库执行错误，回传给 LLM 修复
                error = f"{type(e).__name__}: {e}"
                sql = raw_sql

        if error:
            return {
                "success": False,
                "question": question,
                "error": f"SQL 生成/执行失败（已尝试 {attempts} 次）: {error}",
                "sql": sql,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

        # 4. 成功的问答对自动沉淀到查询记忆（Vanna 思想）
        self.memory.add(question, sql)

        # 5. 结果加工
        summary = summarize(question, sql, columns, rows)
        return {
            "success": True,
            "question": question,
            "sql": sql,
            "tables_used": tables,
            "memory_used": bool(examples),
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(rows) >= settings.max_rows,
            "chart": suggest_chart(columns, rows),
            "answer": summary["answer"],
            "followups": summary["followups"],
            "attempts": attempts,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
