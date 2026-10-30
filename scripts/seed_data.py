"""演示数据种子脚本：创建电商演示库（客户/商品/订单/订单明细）并灌入模拟数据。

用法:
    python scripts/seed_data.py

数据写入 .env 里 DATABASE_URL 指向的库（默认 nl2sql_demo.db）。
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, Text  # noqa: E402
from sqlalchemy.orm import declarative_base  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.config import settings  # noqa: E402

Base = declarative_base()


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, autoincrement=True, comment="客户ID")
    name = Column(String(50), nullable=False, comment="客户姓名")
    city = Column(String(30), nullable=False, comment="所在城市")
    gender = Column(String(2), nullable=False, comment="性别：男/女")
    register_date = Column(Date, nullable=False, comment="注册日期")


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, autoincrement=True, comment="商品ID")
    name = Column(String(80), nullable=False, comment="商品名称")
    category = Column(String(20), nullable=False, comment="品类")
    price = Column(Float, nullable=False, comment="当前售价")
    stock = Column(Integer, nullable=False, comment="当前库存")
    description = Column(Text, comment="商品描述")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True, comment="订单ID")
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, comment="客户ID")
    order_date = Column(Date, nullable=False, comment="下单日期")
    status = Column(String(10), nullable=False, comment="状态：已完成/已发货/待发货/已取消")
    total_amount = Column(Float, nullable=False, comment="订单总金额")


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, autoincrement=True, comment="明细ID")
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, comment="订单ID")
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, comment="商品ID")
    quantity = Column(Integer, nullable=False, comment="数量")
    unit_price = Column(Float, nullable=False, comment="成交单价")


FIRST_NAMES = "伟芳娜秀敏静丽强磊军洋勇艳杰娟涛明超霞平刚桂香兰梅鑫航晨悦彤轩"
LAST_NAMES = "王李张刘陈杨黄赵周吴徐孙马朱胡郭何林罗高郑梁谢宋唐许"
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "呼和浩特"]
CATEGORIES = {
    "数码": ["无线蓝牙耳机", "智能手表", "机械键盘", "便携充电宝", "4K显示器", "智能音箱", "电竞鼠标", "降噪头戴耳机"],
    "家居": ["乳胶枕", "香薰蜡烛", "收纳箱套装", "加湿器", "懒人沙发", "落地灯", "记忆棉床垫"],
    "服饰": ["纯棉T恤", "牛仔外套", "羽绒服", "运动卫衣", "休闲长裤", "连衣裙", "帆布鞋"],
    "美妆": ["保湿面霜", "氨基酸洁面", "防晒霜SPF50", "口红礼盒", "精华液", "面膜20片装"],
    "食品": ["坚果大礼包", "有机牛奶12盒", "精品咖啡豆", "全麦面包", "坚果酸奶", "黑巧克力礼盒"],
}
STATUSES = ["已完成", "已完成", "已完成", "已发货", "待发货", "已取消"]  # 加权：完成概率最高


def gen_customers(n=500):
    rng = random.Random(42)
    rows = []
    for _ in range(n):
        name = rng.choice(LAST_NAMES) + rng.choice(FIRST_NAMES) + (rng.choice(FIRST_NAMES) if rng.random() < 0.3 else "")
        rows.append(Customer(
            name=name,
            city=rng.choice(CITIES),
            gender=rng.choice(["男", "女"]),
            register_date=date(2023, 1, 1) + timedelta(days=rng.randint(0, 1000)),
        ))
    return rows


def gen_products():
    rows, pid = [], 1
    for cat, names in CATEGORIES.items():
        rng = random.Random(hash(cat) % 1000)
        for name in names:
            rows.append(Product(
                id=pid,
                name=name,
                category=cat,
                price=round(rng.uniform(19, 2999), 1),
                stock=rng.randint(0, 500),
                description=f"{cat}类商品：{name}",
            ))
            pid += 1
    return rows


def gen_orders(customers, products, n=3000):
    """2024-01 ~ 2026-10，带 11 月大促的季节性。"""
    rng = random.Random(2026)
    rows, items = [], []
    order_id = 1
    for _ in range(n):
        cust = rng.choice(customers)
        # 月份权重：11月最高（模拟双11大促）
        month_weights = {1: 4, 2: 3, 3: 5, 4: 5, 5: 6, 6: 6, 7: 6, 8: 6, 9: 7, 10: 8, 11: 12, 12: 9}
        year = rng.choices([2024, 2025, 2026], weights=[30, 40, 30])[0]
        month = rng.choices(list(month_weights), weights=list(month_weights.values()))[0]
        if year == 2026 and month > 10:
            month = rng.randint(1, 10)
        day = rng.randint(1, 28)
        odate = date(year, month, day)

        status = rng.choice(STATUSES)
        n_items = rng.randint(1, 4)
        total = 0.0
        for _ in range(n_items):
            p = rng.choice(products)
            qty = rng.randint(1, 3)
            unit_price = round(p.price * rng.uniform(0.8, 1.0), 1)
            total += qty * unit_price
            items.append(OrderItem(
                order_id=order_id, product_id=p.id, quantity=qty, unit_price=unit_price,
            ))
        rows.append(Order(
            id=order_id, customer_id=cust.id, order_date=odate,
            status=status, total_amount=round(total, 2),
        ))
        order_id += 1
    return rows, items


def main():
    engine = create_engine(settings.database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    customers = gen_customers()
    products = gen_products()

    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.add_all(customers)
        s.add_all(products)
        s.flush()  # 先拿到自动生成的 id，再构建订单
        orders, items = gen_orders(customers, products)
        s.add_all(orders)
        s.add_all(items)
        s.commit()

    print(f"演示数据已写入: {settings.database_url}")
    print(f"  customers={len(customers)}  products={len(products)}  orders={len(orders)}  order_items={len(items)}")


if __name__ == "__main__":
    main()
