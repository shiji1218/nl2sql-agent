"""Schema 内省：从数据库读取表结构，并叠加业务文档层（schema_docs.json）。"""
import json
from functools import lru_cache

from sqlalchemy import create_engine, inspect

from app.config import settings


class SchemaInspector:
    def __init__(self) -> None:
        self.engine = create_engine(settings.database_url)
        self.docs = self._load_docs()

    def _load_docs(self) -> dict:
        if settings.schema_docs_path.exists():
            return json.loads(settings.schema_docs_path.read_text(encoding="utf-8"))
        return {"tables": {}}

    def _table_docs(self, table: str) -> tuple[str, dict]:
        t = self.docs.get("tables", {}).get(table, {})
        return t.get("description", ""), t.get("columns", {})

    def get_table_names(self) -> list[str]:
        insp = inspect(self.engine)
        # 过滤掉 SQLite 的内部表 / MySQL 系统库
        return [
            t for t in insp.get_table_names()
            if not t.startswith("sqlite_")
        ]

    def get_table_ddl(self, table: str) -> str:
        """渲染一份『人读 + 模型读』都友好的表结构描述（含业务注释）。"""
        insp = inspect(self.engine)
        cols = insp.get_columns(table)
        fks = insp.get_foreign_keys(table)
        fk_map = {}
        for fk in fks:
            for col in fk.get("constrained_columns", []):
                fk_map[col] = f"{fk['referred_table']}.{fk['referred_columns'][0]}"

        desc, col_docs = self._table_docs(table)
        lines = [f"TABLE {table}  -- {desc}" if desc else f"TABLE {table}"]
        for c in cols:
            parts = [f"  {c['name']} {str(c['type'])}"]
            note = col_docs.get(c["name"], "")
            if c.get("primary_key"):
                parts.append("PRIMARY KEY")
            if note:
                parts.append(f"-- {note}")
            if c["name"] in fk_map:
                parts.append(f"[FK -> {fk_map[c['name']]}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def schema_summary(self) -> str:
        """所有表的一行摘要，用于『表路由』阶段。"""
        out = []
        for t in self.get_table_names():
            desc, _ = self._table_docs(t)
            out.append(f"- {t}: {desc}" if desc else f"- {t}")
        return "\n".join(out)

    def metrics_text(self) -> str:
        """业务指标口径（迷你语义层），注入生成器提示词。"""
        m = self.docs.get("_metrics", {})
        if not m:
            return ""
        lines = ["业务指标口径（生成 SQL 时必须严格遵守）："]
        lines += [f"- {k}：{v}" for k, v in m.items()]
        return "\n".join(lines)

    def full_schema(self) -> str:
        """全部表的完整 DDL 文本。"""
        return "\n\n".join(self.get_table_ddl(t) for t in self.get_table_names())


@lru_cache
def get_inspector() -> SchemaInspector:
    # 引擎按 database_url 缓存，避免每次请求重建连接池
    return SchemaInspector()
