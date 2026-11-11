"""部署后冒烟：健康检查 → 访问码 → 示例数据 → 确定性出图。

用法：

    python scripts/smoke_demo.py https://xxx.onrender.com 你的访问码

不传访问码时，适用于未设门禁的部署。
"""

from __future__ import annotations

import argparse
import sys

import requests


def check(base: str, code: str) -> int:
    base = base.rstrip("/")
    session = requests.Session()

    health = session.get(f"{base}/health", timeout=60)
    print(f"[1/5] /health -> {health.status_code}")
    if health.status_code != 200:
        print("  平台探活失败，服务可能还在唤醒或启动失败。")
        return 1

    info = session.get(f"{base}/api/health", timeout=60).json()
    print(
        f"[2/5] API 就绪：模型={info.get('model')} "
        f"演示={info.get('demo_enabled')} 门禁={info.get('access_required')} "
        f"上传上限={info.get('max_file_mb')} MB "
        f"AI 配额={info.get('llm_rate_limit') or '不限'}/分·"
        f"{info.get('llm_daily_limit') or '不限'}/天"
    )

    if code:
        login = session.post(f"{base}/api/access", json={"code": code}, timeout=30)
        print(f"[3/5] 访问码 -> {login.status_code}")
        if login.status_code != 200:
            print(f"  验证失败：{login.text[:200]}")
            return 1
    else:
        print("[3/5] 跳过访问码（未提供）")

    demo = session.post(f"{base}/api/demo", timeout=120)
    print(f"[4/5] 示例数据 -> {demo.status_code}")
    if demo.status_code != 200:
        print(f"  载入失败：{demo.text[:300]}")
        return 1
    dataset = demo.json()
    print(f"  {dataset['rows']} 行 × {dataset['columns']} 列 · {dataset['filename']}")

    specs = session.get(f"{base}/api/datasets/{dataset['dataset_id']}/specs", timeout=60).json()
    if not specs:
        print("  没有推荐图表，跳过出图检查。")
        return 0
    spec = dict(specs[0]["spec"])
    spec.pop("title", None)
    chart = session.post(
        f"{base}/api/chart/spec",
        json={"dataset_id": dataset["dataset_id"], "spec": spec},
        timeout=120,
    )
    print(f"[5/5] 确定性出图 -> {chart.status_code}")
    if chart.status_code != 200:
        print(f"  出图失败：{chart.text[:300]}")
        return 1
    payload = chart.json()
    print(
        f"  {specs[0]['label']} · {payload.get('points')} 点 · "
        f"{len(payload.get('chart_json') or '')} 字符图表数据"
    )
    print("\n全部通过：这个链接可以发给 HR。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DataAgent Pro 公网冒烟")
    parser.add_argument("base_url", help="例如 https://dataagent-pro.onrender.com")
    parser.add_argument("code", nargs="?", default="", help="访问码（未设门禁可省略）")
    args = parser.parse_args()
    try:
        return check(args.base_url, args.code)
    except requests.RequestException as exc:
        print(f"请求失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
