"""查询分解器（Query Decomposition）：把复合问题拆成可独立执行的子问题。

"2016年9月到12月销售额最高的5个品类，以及各支付方式的订单数"
    → ["2016年9月到12月销售额最高的5个品类", "各支付方式的订单数"]

拆分原则：
- 只有当问题包含 ≥2 个互不依赖、需要分别写 SQL 的数据问题时才拆分
- 一条 SQL 就能同时回答的多指标问题（如"提前和延迟送达的比例"）不拆
- 最多 3 个子问题
"""
import json

from app.agent.answer import strip_json_fence
from app.llm.client import chat

SYSTEM_PROMPT = """你是数据查询规划器。判断用户的问题是否需要拆分成多个子查询。

拆分规则：
1. 只有当问题包含两个及以上互不依赖、需要分别写 SQL 查询的数据问题时才拆分。
2. 一条 SQL 就能同时回答的不拆（例如"提前送达和延迟送达的比例"只是一张表的两个指标）。
3. 拆分时把每个子问题改写成独立完整、可单独理解的问题（补全省略的上下文）。
4. 最多拆成 3 个子问题。
5. 单一问题必须原样返回（数组里只有它自己）。

只输出 JSON 数组，不要解释。示例：
输入: "上个月卖得最好的商品是什么？顺便看看总销售额多少"
输出: ["上个月卖得最好的商品是什么", "上个月的总销售额是多少"]
"""


def decompose(question: str, schema_summary: str) -> list[str]:
    """返回子问题列表；单一问题返回 [原问题]。任何异常都降级为不拆分。"""
    try:
        user = f"数据库表清单:\n{schema_summary}\n\n用户问题: {question}"
        raw = chat(SYSTEM_PROMPT, user, temperature=0.0)
        arr = json.loads(strip_json_fence(raw))
        if not (isinstance(arr, list) and arr):
            return [question]
        subs = [str(x).strip() for x in arr if str(x).strip()]
        if not subs or len(subs) > 3 or subs == [question]:
            return [question]
        # 防幻觉：子问题不应与原问题完全无关（字符重合度过低视为解析失败）
        for s in subs:
            if not set(question) & set(s):
                return [question]
        return subs
    except Exception:  # noqa: BLE001 —— 分解失败不阻断主流程
        return [question]
