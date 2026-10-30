"""SQL 生成器：将 schema + 问题(+报错反馈) 交给 LLM 生成 SQL。"""
from app.config import settings
from app.llm.client import chat, strip_code_fence

SYSTEM_PROMPT = """你是一位资深的 {dialect} 数据分析专家，任务是把用户的自然语言问题转换成一条 {dialect} 查询语句。

规则：
1. 只输出一条 SQL，不要任何解释，不要 markdown 代码块。
2. 只能是只读查询（SELECT 或 WITH 开头），绝不输出增删改。
3. 表名、列名必须严格来自下方提供的 schema，不要凭空编造。
4. 字符串完全匹配优先用 LIKE，中文匹配注意可能有前后缀。
5. 聚合结果记得起别名（AS 中文别名也可以）。
6. 日期用标准比较，如 order_date >= '2025-01-01'；按月分组可用 substr(日期列,1,7)。
7. 聚合/分组查询必须带 ORDER BY（按分组列或聚合值），保证结果顺序稳定；TopN 类按聚合值降序。
8. TopN 类问题（最高N/前N/最好N）必须带 LIMIT N；趋势、占比、全量分组明细类不要写 LIMIT（系统会自动控制行数）。

Few-shot 示例：
问题: 去年每个月的销售额趋势
SQL: SELECT substr(order_date,1,7) AS 月份, SUM(total_amount) AS 销售额 FROM orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY 月份 ORDER BY 月份
问题: 卖得最好的5个商品是哪些
SQL: SELECT p.name AS 商品, SUM(oi.quantity * oi.unit_price) AS 销售额 FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.name ORDER BY 销售额 DESC LIMIT 5
问题: 各订单状态的数量分布
SQL: SELECT status AS 状态, COUNT(*) AS 订单数 FROM orders GROUP BY status ORDER BY 订单数 DESC
"""

REPAIR_SUFFIX = """
上一次生成的 SQL 执行出错，请根据错误信息修正后重新输出一条 SQL（同样只输出 SQL 本身）。

上一次的 SQL:
{prev_sql}

数据库报错:
{error}
"""


def generate_sql(
    schema_ddl: str,
    question: str,
    history: str = "",
    prev_sql: str = "",
    error: str = "",
    examples: str = "",
) -> str:
    system = SYSTEM_PROMPT.format(dialect=settings.dialect)
    parts = [f"数据库 schema:\n{schema_ddl}"]
    if examples:
        parts.append(examples)
    if history:
        parts.append(f"对话历史（可用于理解指代，如'再看按月拆分'）:\n{history}")
    user = "\n\n".join(parts) + f"\n\n用户问题: {question}"

    if prev_sql and error:
        user += REPAIR_SUFFIX.format(prev_sql=prev_sql, error=error)

    return strip_code_fence(chat(system, user, temperature=0.0))
