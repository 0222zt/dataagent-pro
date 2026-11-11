"""生成 DataAgent Pro 演示用的模拟销售明细表。

确定性随机（固定种子），重复运行结果一致：
    python scripts/generate_sample_data.py

输出：
    sample_data/示例数据_销售明细.csv   （UTF-8 BOM，Excel 直接双击不乱码）
    sample_data/示例数据_销售明细.xlsx  （带冻结表头、筛选、列宽、金额格式）
"""

from __future__ import annotations

import calendar
import random
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BASE_DIR / "sample_data"

REGIONS: dict[str, list[str]] = {
    "华东": ["上海", "杭州", "南京", "苏州", "宁波"],
    "华北": ["北京", "天津", "青岛", "石家庄", "太原"],
    "华南": ["广州", "深圳", "东莞", "厦门", "南宁"],
    "西南": ["成都", "重庆", "昆明", "贵阳", "绵阳"],
    "华中": ["武汉", "长沙", "郑州", "合肥", "南昌"],
}
REGION_WEIGHTS = [0.30, 0.24, 0.22, 0.12, 0.12]

CATEGORIES: dict[str, dict[str, object]] = {
    "家用电器": {
        "products": ["空气炸锅", "扫地机器人", "电饭煲", "净水器", "取暖器"],
        "price": 899,
        "qty": (1, 5),
        "cost": 0.68,
    },
    "数码配件": {
        "products": ["无线耳机", "移动电源", "机械键盘", "摄像头", "扩展坞"],
        "price": 329,
        "qty": (1, 8),
        "cost": 0.55,
    },
    "办公用品": {
        "products": ["A4 复印纸", "人体工学椅", "白板", "文件柜", "打印机耗材"],
        "price": 159,
        "qty": (2, 20),
        "cost": 0.62,
    },
    "家居生活": {
        "products": ["乳胶枕", "四件套", "香薰机", "收纳箱", "落地灯"],
        "price": 259,
        "qty": (1, 10),
        "cost": 0.58,
    },
    "户外运动": {
        "products": ["登山杖", "折叠椅", "露营灯", "运动水壶", "速干衣"],
        "price": 199,
        "qty": (1, 12),
        "cost": 0.60,
    },
}
CATEGORY_WEIGHTS = [0.24, 0.22, 0.20, 0.20, 0.14]

CHANNELS = ["线上商城", "线下门店", "经销商", "直播带货"]
CHANNEL_WEIGHTS = [0.38, 0.28, 0.18, 0.16]

SALESPEOPLE = ["王磊", "李娜", "张伟", "陈静", "刘洋", "赵敏", "孙浩", "周婷"]

ROWS = 360
MISSING_RATING_RATIO = 0.02


def month_weights() -> list[float]:
    # 模拟旺季：9-12 月权重更高，2 月偏低
    return [0.07, 0.05, 0.08, 0.07, 0.08, 0.08, 0.08, 0.08, 0.09, 0.10, 0.11, 0.11]


def build_frame() -> pd.DataFrame:
    rng = np.random.default_rng(20261111)
    random.seed(20261111)

    months = [date(2025, month, 1) for month in range(1, 13)]
    weights = month_weights()

    rows: list[dict[str, object]] = []
    for _ in range(ROWS):
        month_start = random.choices(months, weights=weights, k=1)[0]
        day_limit = calendar.monthrange(month_start.year, month_start.month)[1]
        day = random.randint(1, day_limit)
        order_date = date(month_start.year, month_start.month, day)

        region = random.choices(list(REGIONS), weights=REGION_WEIGHTS, k=1)[0]
        city = random.choice(REGIONS[region])
        category = random.choices(list(CATEGORIES), weights=CATEGORY_WEIGHTS, k=1)[0]
        spec = CATEGORIES[category]
        product = random.choice(spec["products"])

        base_price = float(spec["price"])
        price = round(base_price * rng.normal(1.0, 0.05), 2)
        price = max(9.9, price)

        low, high = spec["qty"]
        qty = int(rng.integers(low, high + 1))
        # 大额订单更少见：数量越高，折扣概率越大
        discount = 0.0
        if qty >= high - 1 or random.random() < 0.35:
            discount = round(min(0.25, max(0.0, rng.normal(0.08, 0.05))), 2)

        amount = round(qty * price * (1 - discount), 2)
        cost_ratio = float(spec["cost"]) + float(rng.normal(0, 0.03))
        cost = round(amount * min(0.85, max(0.4, cost_ratio)), 2)
        profit = round(amount - cost, 2)
        margin = round(profit / amount, 4) if amount else 0.0

        rating: float | None = round(float(np.clip(rng.normal(4.4, 0.35), 3.0, 5.0)), 1)
        if random.random() < MISSING_RATING_RATIO:
            rating = None

        rows.append(
            {
                "订单号": "",
                "日期": order_date,
                "月份": order_date.strftime("%Y-%m"),
                "区域": region,
                "城市": city,
                "产品类别": category,
                "产品名称": product,
                "销售渠道": random.choices(
                    CHANNELS, weights=CHANNEL_WEIGHTS, k=1
                )[0],
                "销售员": random.choice(SALESPEOPLE),
                "数量": qty,
                "单价": price,
                "折扣": discount,
                "销售额": amount,
                "成本": cost,
                "毛利": profit,
                "毛利率": margin,
                "客户评分": rating,
                "是否新客": "是" if random.random() < 0.3 else "否",
                "是否促销": "是" if discount > 0 else "否",
            }
        )

    frame = pd.DataFrame(rows).sort_values("日期").reset_index(drop=True)
    frame["订单号"] = [f"SO2025{index:05d}" for index in range(1, len(frame) + 1)]
    return frame


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    # utf-8-sig 让 Excel 双击打开时不乱码
    frame.to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")


def write_excel(frame: pd.DataFrame, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl", date_format="yyyy-mm-dd") as writer:
        frame.to_excel(writer, index=False, sheet_name="销售明细")
        sheet = writer.sheets["销售明细"]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        for index, column in enumerate(frame.columns, start=1):
            sample = frame[column].astype(str).str.len().max()
            width = max(10, min(20, max(len(column) + 4, int(sample) + 2)))
            sheet.column_dimensions[get_column_letter(index)].width = width

        money_columns = {"单价", "销售额", "成本", "毛利"}
        percent_columns = {"折扣", "毛利率"}
        for index, column in enumerate(frame.columns, start=1):
            letter = get_column_letter(index)
            if column in money_columns:
                for cell in sheet[letter][1:]:
                    cell.number_format = "#,##0.00"
            elif column in percent_columns:
                for cell in sheet[letter][1:]:
                    cell.number_format = "0.0%"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = build_frame()

    csv_path = OUT_DIR / "示例数据_销售明细.csv"
    xlsx_path = OUT_DIR / "示例数据_销售明细.xlsx"
    write_csv(frame, csv_path)
    write_excel(frame, xlsx_path)

    print(f"已生成 {len(frame)} 行 × {len(frame.columns)} 列")
    print(f"CSV : {csv_path}  ({csv_path.stat().st_size / 1024:.1f} KB)")
    print(f"XLSX: {xlsx_path}  ({xlsx_path.stat().st_size / 1024:.1f} KB)")
    print(f"日期范围: {frame['日期'].min()} → {frame['日期'].max()}")
    print(f"销售额合计: {frame['销售额'].sum():,.2f} 元")
    print(frame.head(3).to_string(index=False))


if __name__ == "__main__":
    main()
