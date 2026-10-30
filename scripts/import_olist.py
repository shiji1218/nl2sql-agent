"""Olist 巴西电商数据集导入脚本。

将 data/olist/ 下的 9 个 CSV 导入 MySQL 的 olist 库：
- 建表时写入真实 MySQL COMMENT（中文业务含义），schema 内省可直接读到
- 地理表按邮编前缀去重（190 万行 → 约 2 万行，问数场景不需要原始精度）
- 分批 executemany 插入

用法:
    python scripts/import_olist.py
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import (  # noqa: E402
    Column, Date, DateTime, Double, ForeignKey, Integer, MetaData, String,
    Table, Text, create_engine, event, text,
)

from app.config import settings  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "olist"
CHUNK = 2000

metadata = MetaData()

customers = Table(
    "customers", metadata,
    Column("customer_id", String(32), primary_key=True, comment="客户ID（每笔订单对应一个）"),
    Column("customer_unique_id", String(32), comment="客户唯一ID（同一真实客户不变，算复购用它）"),
    Column("customer_zip_code_prefix", String(10), comment="邮编前缀"),
    Column("customer_city", String(80), comment="所在城市"),
    Column("customer_state", String(2), comment="所在州（巴西州缩写，如SP圣保罗）"),
)
orders = Table(
    "orders", metadata,
    Column("order_id", String(32), primary_key=True, comment="订单ID"),
    Column("customer_id", String(32), ForeignKey("customers.customer_id"), comment="下单客户ID"),
    Column("order_status", String(20), comment="订单状态：delivered已送达/shipped运输中/canceled已取消等"),
    Column("order_purchase_timestamp", DateTime, comment="下单时间"),
    Column("order_approved_at", DateTime, comment="支付审批时间"),
    Column("order_delivered_carrier_date", DateTime, comment="交付物流商时间"),
    Column("order_delivered_customer_date", DateTime, comment="客户签收时间"),
    Column("order_estimated_delivery_date", DateTime, comment="预计送达时间（与实际签收对比可算履约延迟）"),
)
order_items = Table(
    "order_items", metadata,
    Column("order_id", String(32), ForeignKey("orders.order_id"), primary_key=True, comment="订单ID"),
    Column("order_item_id", Integer, primary_key=True, comment="订单内商品行号（1开始，行数即商品件数）"),
    Column("product_id", String(32), ForeignKey("products.product_id"), comment="商品ID"),
    Column("seller_id", String(32), ForeignKey("sellers.seller_id"), comment="卖家ID"),
    Column("shipping_limit_date", DateTime, comment="卖家发货截止时间"),
    Column("price", Double, comment="商品成交价（元/雷亚尔），商品维度销售额=SUM(price)"),
    Column("freight_value", Double, comment="运费"),
)
order_payments = Table(
    "order_payments", metadata,
    Column("order_id", String(32), ForeignKey("orders.order_id"), primary_key=True, comment="订单ID"),
    Column("payment_sequential", Integer, primary_key=True, comment="支付序号（一笔订单可多次支付）"),
    Column("payment_type", String(20), comment="支付方式：credit_card信用卡/boleto巴西票据/voucher代金券/debit_card借记卡"),
    Column("payment_installments", Integer, comment="分期期数（巴西特色，0或1为不分期）"),
    Column("payment_value", Double, comment="本笔支付金额（含运费）"),
)
order_reviews = Table(
    "order_reviews", metadata,
    Column("review_id", String(32), primary_key=True, comment="评价ID"),
    Column("order_id", String(32), ForeignKey("orders.order_id"), comment="订单ID"),
    Column("review_score", Integer, comment="评分1-5（1最差5最好），口碑分析核心字段"),
    Column("review_comment_title", String(120), comment="评论标题（葡语，可空）"),
    Column("review_comment_message", Text, comment="评论正文（葡语，可空）"),
    Column("review_creation_date", Date, comment="评价发起日期"),
    Column("review_answer_timestamp", DateTime, comment="问卷回收时间"),
)
products = Table(
    "products", metadata,
    Column("product_id", String(32), primary_key=True, comment="商品ID"),
    Column("product_category_name", String(60), comment="品类名（葡萄牙语）"),
    Column("product_name_lenght", Integer, comment="商品名字符数"),
    Column("product_description_lenght", Integer, comment="商品描述字符数"),
    Column("product_photos_qty", Integer, comment="商品图片数"),
    Column("product_weight_g", Integer, comment="重量（克）"),
    Column("product_length_cm", Integer, comment="长（厘米）"),
    Column("product_height_cm", Integer, comment="高（厘米）"),
    Column("product_width_cm", Integer, comment="宽（厘米）"),
)
sellers = Table(
    "sellers", metadata,
    Column("seller_id", String(32), primary_key=True, comment="卖家ID"),
    Column("seller_zip_code_prefix", String(10), comment="卖家邮编前缀"),
    Column("seller_city", String(80), comment="卖家城市"),
    Column("seller_state", String(2), comment="卖家州"),
)
geolocation = Table(
    "geolocation", metadata,
    Column("geolocation_zip_code_prefix", String(10), primary_key=True, comment="邮编前缀（已按前缀去重）"),
    Column("geolocation_lat", Double, comment="纬度"),
    Column("geolocation_lng", Double, comment="经度"),
    Column("geolocation_city", String(80), comment="城市"),
    Column("geolocation_state", String(2), comment="州"),
)
category_translation = Table(
    "product_category_name_translation", metadata,
    Column("product_category_name", String(60), primary_key=True, comment="品类名（葡萄牙语）"),
    Column("product_category_name_english", String(60), comment="品类名（英语，查询请关联此表用英文品类）"),
)

# CSV 文件 → 表 的导入配置（按依赖顺序：先父表后子表）
IMPORTS = [
    ("olist_customers_dataset.csv", customers),
    ("olist_sellers_dataset.csv", sellers),
    ("product_category_name_translation.csv", category_translation),
    ("olist_products_dataset.csv", products),
    ("olist_orders_dataset.csv", orders),
    ("olist_order_items_dataset.csv", order_items),
    ("olist_order_payments_dataset.csv", order_payments),
]


def _convert(col, value: str):
    """空串→NULL；按列类型转换数字/日期，其余原样字符串。"""
    if value is None or value == "":
        return None
    t = col.type
    if isinstance(t, (Integer,)):
        return int(float(value))
    if isinstance(t, Double):
        return float(value)
    return value


def load_csv(path: Path, table: Table):
    """生成器：逐行读取 CSV 并按表结构转换。"""
    with open(path, newline="", encoding="utf-8-sig") as f:  # utf-8-sig 处理 BOM
        reader = csv.reader(f)
        header = next(reader)
        cols = [table.c[name] for name in header]
        for row in reader:
            yield {c.name: _convert(c, v) for c, v in zip(cols, row)}


def import_table(engine, path: Path, table: Table) -> int:
    n = 0
    buf: list[dict] = []
    with engine.begin() as conn:
        for record in load_csv(path, table):
            buf.append(record)
            if len(buf) >= CHUNK:
                conn.execute(table.insert(), buf)
                n += len(buf)
                buf = []
        if buf:
            conn.execute(table.insert(), buf)
            n += len(buf)
    return n


def main():
    engine = create_engine(settings.database_url)
    # 批量导入期间关闭外键检查（Olist 原始数据存在少量孤儿引用，属已知数据质量问题）
    event.listen(engine, "connect", lambda dbapi_conn, _: dbapi_conn.cursor().execute("SET FOREIGN_KEY_CHECKS=0"))
    print(f"目标库: {settings.database_url.split('@')[-1]}")
    metadata.drop_all(engine)
    metadata.create_all(engine)

    for filename, table in IMPORTS:
        t0 = time.time()
        n = import_table(engine, DATA_DIR / filename, table)
        print(f"  {table.name:40s} {n:>8} 行  ({time.time()-t0:.1f}s)")

    # 地理表：按邮编前缀去重后导入
    t0 = time.time()
    seen: dict[str, dict] = {}
    for record in load_csv(DATA_DIR / "olist_geolocation_dataset.csv", geolocation):
        seen.setdefault(record["geolocation_zip_code_prefix"], record)
    rows = list(seen.values())
    with engine.begin() as conn:
        for i in range(0, len(rows), CHUNK):
            conn.execute(geolocation.insert(), rows[i : i + CHUNK])
    print(f"  {'geolocation(去重后)':38s} {len(rows):>8} 行  ({time.time()-t0:.1f}s)")

    # 评价表：CSV 中存在重复 review_id（数据集已知问题），按 review_id 去重
    t0 = time.time()
    seen = {}
    for record in load_csv(DATA_DIR / "olist_order_reviews_dataset.csv", order_reviews):
        seen.setdefault(record["review_id"], record)
    rows = list(seen.values())
    with engine.begin() as conn:
        for i in range(0, len(rows), CHUNK):
            conn.execute(order_reviews.insert(), rows[i : i + CHUNK])
    print(f"  {'order_reviews(去重后)':37s} {len(rows):>8} 行  ({time.time()-t0:.1f}s)")

    print("Olist 导入完成")


if __name__ == "__main__":
    main()
