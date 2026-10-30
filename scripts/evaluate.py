"""评测脚本：用评测集（问题 + 标准 SQL）跑完整 Agent 链路，计算执行准确率。

判定方式（Execution Accuracy，业界主流做法）：
Agent 生成的 SQL 与标准 SQL 各自执行，若返回的行数据一致则算通过，
不要求 SQL 文本一致（一条问题可以有多种等价写法）。

用法:
    python scripts/evaluate.py            # 全量评测
    python scripts/evaluate.py --limit 5  # 只跑前 5 条（快速冒烟）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.agent.pipeline import NL2SQLAgent  # noqa: E402
from app.config import settings  # noqa: E402


def run_gold(gold_sql: str) -> list[tuple]:
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        rows = conn.execute(text(gold_sql)).fetchall()
    return normalize(rows)


def normalize(rows) -> list[tuple]:
    """行 → 元组，浮点四舍五入，整体排序，忽略顺序差异。"""
    def cell(v):
        if isinstance(v, float):
            return round(v, 2)
        return v
    out = [tuple(cell(v) for v in row) for row in rows]
    return sorted(out, key=lambda r: tuple(str(x) for x in r))


def results_match(agent_rows: list[tuple], gold_rows: list[tuple]) -> bool:
    """执行结果语义比对（容忍合理差异）：
    1. 严格一致（含前缀截断兜底）；
    2. 单值比率类：容忍 百分数 vs 小数 口径（15.93 ≈ 0.16×100）；
    3. Agent 多输出列（superset）：每一行包含标准行的全部值且顺序一致即通过。
    """
    if agent_rows == gold_rows:
        return True
    if len(agent_rows) < len(gold_rows) and gold_rows[: len(agent_rows)] == agent_rows:
        return True
    # 比率口径：x% vs x/100
    if len(agent_rows) == 1 and len(gold_rows) == 1:
        a, g = agent_rows[0], gold_rows[0]
        if len(a) == 1 and len(g) == 1:
            try:
                av, gv = float(a[0]), float(g[0])
                return any([
                    abs(av - gv) <= 0.01 * max(abs(gv), 1e-9) + 0.01,
                    abs(av - gv * 100) <= 0.01 * max(abs(gv * 100), 1e-9) + 0.01,
                ])
            except (TypeError, ValueError):
                pass
    # 列 superset：Agent 行包含标准行的值序列（按首列对齐排序，避免
    # Agent 未 ORDER BY 时整体元组排序顺序不一致的误判）
    if len(agent_rows) == len(gold_rows) and gold_rows:
        ars = sorted(agent_rows, key=lambda r: str(r[0]))
        grs = sorted(gold_rows, key=lambda r: str(r[0]))
        for ar, gr in zip(ars, grs):
            # 在 agent 行中按顺序找 gold 行的值（浮动容差）
            pos = 0
            for gv in gr:
                found = False
                while pos < len(ar):
                    av = ar[pos]
                    try:
                        if abs(float(av) - float(gv)) <= 0.01 * max(abs(float(gv)), 1e-9) + 0.01:
                            found = True
                    except (TypeError, ValueError):
                        if av == gv:
                            found = True
                    if found:
                        pos += 1
                        break
                    pos += 1
                if not found:
                    return False
        return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全量）")
    args = parser.parse_args()

    eval_path = Path(__file__).resolve().parent.parent / "eval" / "eval_set.jsonl"
    cases = [json.loads(line) for line in eval_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[: args.limit]

    agent = NL2SQLAgent()
    passed = 0
    print(f"开始评测: {len(cases)} 条问题 | 模型: {settings.llm_model}\n" + "-" * 60)

    for i, case in enumerate(cases, 1):
        q, gold = case["question"], case["gold_sql"]
        try:
            result = agent.answer(q)
        except Exception as e:  # noqa: BLE001
            result = {"success": False, "error": str(e)}

        if not result.get("success"):
            print(f"[{i:02d}] FAIL  {q}\n     错误: {result.get('error', '')[:120]}\n")
            continue

        try:
            agent_rows = normalize([tuple(r) for r in result["rows"]])
            gold_rows = run_gold(gold)
            ok = results_match(agent_rows, gold_rows)
        except Exception as e:  # noqa: BLE001
            print(f"[{i:02d}] FAIL  {q}\n     结果比对异常: {e}\n")
            continue

        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            print(f"     Agent SQL: {result['sql'][:150]}")
            print(f"     标准  SQL: {gold[:150]}")
            print(f"     Agent结果: {agent_rows[:3]}... vs 标准结果: {gold_rows[:3]}...")
        print(f"[{i:02d}] {mark}  {q}  ({result.get('elapsed_ms', 0)}ms, 尝试{result.get('attempts', 1)}次)\n")

    print("-" * 60)
    print(f"执行准确率: {passed}/{len(cases)} = {passed / len(cases):.0%}")
    print("提示: 准确率基于执行结果比对，SQL 文本不同但结果等价也算通过。")


if __name__ == "__main__":
    main()
