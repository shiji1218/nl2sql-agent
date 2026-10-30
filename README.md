# NL2SQL Agent · 智能问数

用自然语言查询 MySQL 数据库的 Agent 应用：**schema 内省 → 表路由 → SQL 生成 → 安全护栏 → 自纠错 → 自然语言结论 + 自动图表**。

参考了 GitHub 高热项目的技术路线（Vanna 的 RAG 思想、WrenAI 的语义层、DB-GPT 的 Agentic 自纠错），从零手写核心链路。

## 快速开始

```bash
cd nl2sql-agent

# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置（.env 已就绪则跳过；需要填入你的百炼 API Key）
cp .env.example .env
# 编辑 .env: BAILIAN_API_KEY=sk-xxxx

# 3. 灌入演示数据（默认写入 SQLite，零配置）
python scripts/seed_data.py

# 4. 启动
uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000
```

## 切换到 MySQL

本机没有 MySQL 时任选其一：

```bash
# 方式 A：Docker（推荐）
docker compose up -d

# 方式 B：Homebrew
brew install mysql && brew services start mysql
mysql -u root -e "CREATE DATABASE nl2sql_demo CHARACTER SET utf8mb4; CREATE USER 'nl2sql'@'%' IDENTIFIED BY 'nl2sql123'; GRANT ALL ON nl2sql_demo.* TO 'nl2sql'@'%';"
```

然后修改 `.env`：

```ini
DATABASE_URL=mysql+pymysql://nl2sql:nl2sql123@127.0.0.1:3306/nl2sql_demo?charset=utf8mb4
```

重新执行 `python scripts/seed_data.py` 即可。

## 架构

```
用户问题
   │
   ▼
┌──────────────┐   ⓪ 查询分解：LLM 判断单一/复合问题，复合则拆成 ≤3 个子问题
│ agent/       │      分别执行后汇总结论；子问题自动补全上下文（时间范围等）
│ decomposer.py│
└──────┬───────┘
       ▼
┌──────────────┐   ①Schema 内省：information_schema 读取表结构 + 业务文档层(schema_docs.json)
│ app/db/      │      渲染成 LLM 友好的 DDL 文本
│ inspector.py │
└──────┬───────┘
       ▼
┌──────────────┐   ②表路由：表多时让 LLM 先挑相关表，避免撑爆上下文
│ agent/       │
│ router.py    │
└──────┬───────┘
       ▼
┌──────────────┐   ③SQL 生成：方言感知提示词 + few-shot + 查询记忆检索注入；
│ agent/       │      支持多轮指代（"再按月拆分"）
│ generator.py │
└──────┬───────┘
       ▼
┌──────────────┐   ④安全护栏：只读白名单、单语句、危险关键字黑名单、
│ agent/       │      禁系统库、强制 LIMIT —— 防 prompt 注入拖库删库
│ guard.py     │
└──────┬───────┘
       ▼
┌──────────────┐   ⑤执行 → 失败则把报错回传 LLM 自纠错（最多 N 次）
│ agent/       │
│ pipeline.py  │
└──────┬───────┘
       ▼
┌──────────────┐   ⑥结果加工：图表类型启发式判定（line/bar/pie）+
│ agent/       │      LLM 自然语言结论 + 追问建议
│ answer.py    │
└──────────────┘
```

## 数据集

当前使用 **Olist 巴西电商真实数据集**（Kaggle 公开数据，约 10 万订单 / 2016-2018）：

| 表 | 行数 | 说明 |
|---|---|---|
| orders | 99,441 | 订单主表（状态/下单/签收/预计送达时间） |
| customers | 99,441 | 客户（复购分析用 customer_unique_id） |
| order_items | 112,650 | 订单明细（price=销售额口径，freight=运费） |
| order_payments | 103,886 | 支付（信用卡/boleto票据/分期） |
| order_reviews | 98,410 | 评价（1-5星） |
| products | 32,951 | 商品（葡语品类名） |
| sellers | 3,095 | 卖家 |
| product_category_name_translation | 71 | 品类葡英翻译 |
| geolocation | 19,015 | 地理坐标（已按邮编去重） |

```bash
# 换回/重灌数据集
python scripts/import_olist.py   # Olist（需先将 Kaggle 下载的 9 个 CSV 放入 data/olist/）
python scripts/seed_data.py      # 模拟电商演示库（零下载，适合快速跑通）
```

`config/schema_docs.json` 是**业务文档层 + 指标口径（迷你语义层）**：表字段含义与"销售额/客单价/客户数"等指标口径在此维护并注入提示词——把口径评测驱动的迭代记录：真实数据上首轮 80% → 注入指标口径后 95% → 补齐支付计数口径 100%，这也是 WrenAI/SuperSonic 语义层思想的最小实践。

## 评测

```bash
python scripts/evaluate.py            # 全量 15 题
python scripts/evaluate.py --limit 5  # 快速冒烟
```

采用**执行准确率**（Execution Accuracy）：Agent SQL 与标准 SQL 各自执行，结果一致即通过（不要求文本一致）。评测集在 `eval/eval_set.jsonl`，可持续补充真实问法。

## API

| 端点 | 说明 |
|---|---|
| `POST /api/chat` | `{question, history}` → SQL + 数据 + 结论 + 图表类型（复合问题返回 `sub_results`） |
| `GET /api/schema` | 当前库的 schema 文本 |
| `GET /api/config` | 当前 LLM 配置（Key 打码） |
| `POST /api/config` | 在线更新 LLM 配置，热生效并持久化到 `.env` |
| `POST /api/config/test` | 测试 Key/地址/模型连通性 |
| `GET /api/health` | 健康检查 |

LLM 配置也可以在页面右上角 **⚙ API 配置** 中在线修改（Key/接口地址/模型，带连接测试，保存即热生效，无需重启）。

## Roadmap

- [x] 查询记忆：成功执行的"问题→SQL"对自动沉淀（`data/query_memory.json`），向量检索（text-embedding-v4，bigram 降级）相似问题注入 few-shot——越用越准；`GET/DELETE /api/memory` 管理
- [x] 查询分解：复合问题（"A，以及B"）自动拆成 ≤3 个子问题分别执行、汇总回答，子问题自动补全上下文
- [ ] 指标语义层升级：预定义"销售额/客单价/复购率"等指标口径（YAML），LLM 只做映射
- [ ] 对接 MCP：把查询能力封装成 MCP Server 供 Claude/Cursor 调用
- [x] 连接真实 MySQL 业务库（当前已运行在 MySQL 26.10 上；生产需换只读账号 + 审计日志）
- [ ] 流式输出（SSE）

## 安全说明

- 生产环境务必使用**只读数据库账号**（`GRANT SELECT ONLY`），护栏是纵深防御的第二层而非唯一防线
- `.env` 含 API Key，已加入 `.gitignore`，不要提交
