"""SQL 安全护栏：只读、单语句、危险关键字黑名单、LIMIT 强制注入。

设计原则：即使 LLM 被 prompt 注入（例如用户问题里藏着"删除所有表"），
护栏也保证发往数据库的只能是只读 SELECT。
"""
import re

from app.config import settings

# 词边界匹配，防止误伤列名（如 created_at 含 create 不匹配 \bcreate\b）
_FORBIDDEN = re.compile(
    r"""\b(
        insert|update|delete|drop|alter|truncate|create|replace|
        grant|revoke|call|set|use|lock|unlock|shutdown|kill|
        outfile|dumpfile|load_file|load_data|
        pragma|attach|detach|vacuum|reindex
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
# 系统库：防止拖库/探测
_FORBIDDEN_SCHEMA = re.compile(
    r"\b(information_schema|performance_schema|mysql\.|sys\.)\b",
    re.IGNORECASE,
)
_SELECT_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_LIMIT = re.compile(r"\blimit\s+\d+", re.IGNORECASE)


class SQLGuardError(ValueError):
    """SQL 未通过安全检查。"""


def clean_sql(raw: str) -> str:
    """清理 LLM 输出：去 markdown 围栏、去首尾空白与分号。"""
    sql = raw.strip().strip("`").strip()
    if sql.lower().startswith("sql"):
        sql = sql[3:].strip()
    sql = sql.rstrip(";").strip()
    return sql


def check_sql(sql: str) -> str:
    """校验并返回净化后的 SQL，不合规抛 SQLGuardError。"""
    sql = clean_sql(sql)

    if not sql:
        raise SQLGuardError("SQL 为空")
    if ";" in sql:
        raise SQLGuardError("不允许多条语句")
    if not _SELECT_START.match(sql):
        raise SQLGuardError("只允许 SELECT / WITH 开头的只读查询")
    if _FORBIDDEN.search(sql):
        raise SQLGuardError("包含被禁止的写操作关键字")
    if _FORBIDDEN_SCHEMA.search(sql):
        raise SQLGuardError("不允许访问系统库")

    # 无 LIMIT 则强制注入，防止一次查询拖回百万行
    if not _LIMIT.search(sql):
        sql = f"{sql}\nLIMIT {settings.max_rows}"

    return sql
