#!/usr/bin/env python3
"""闪电.skill 棘轮：测出来的数只许降不许升。

用法：
  python3 ratchet.py init  --result perf/bench/base.json --ceilings perf/bench/ceilings.json
  python3 ratchet.py check --result perf/bench/now.json  --ceilings perf/bench/ceilings.json [--update]

默认比较结果里版本 A 的「能用」p75；--metric 可换成 lcp、requests、bytes_total 等。
check 时超过上限（加容差）就以退出码 1 失败；更低且带 --update 时把上限往下调。
"""
import argparse
import json
import sys


def read_value(result_path, version, metric):
    with open(result_path, encoding="utf-8") as fh:
        data = json.load(fh)
    s = data["results"][version]["summary"]
    return s[metric]["p75"], data["results"][version]["url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["init", "check"])
    ap.add_argument("--result", required=True)
    ap.add_argument("--ceilings", required=True)
    ap.add_argument("--label", help="上限的名字，默认用结果里的 URL")
    ap.add_argument("--version", default="A")
    ap.add_argument("--metric", default="ready")
    ap.add_argument("--tolerance", type=float, default=0.05, help="真实耗时有噪音，默认允许 5%% 波动；计数类指标建议设 0")
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args()

    value, url = read_value(args.result, args.version, args.metric)
    if value is None:
        print("结果里没有这个指标的有效数据，先看 bench 输出里的失败原因", file=sys.stderr)
        sys.exit(2)
    label = args.label or url
    key = f"{label}::{args.metric}_p75"

    try:
        with open(args.ceilings, encoding="utf-8") as fh:
            ceilings = json.load(fh)
    except FileNotFoundError:
        ceilings = {}

    if args.action == "init":
        ceilings[key] = value
        print(f"上限已记下：{key} = {value}")
    else:
        if key not in ceilings:
            print(f"没有 {key} 的上限，先跑 init", file=sys.stderr)
            sys.exit(2)
        limit = ceilings[key]
        allowed = limit * (1 + args.tolerance)
        if value > allowed:
            print(f"✗ 变差了：{key} = {value}，上限 {limit}（容差内最多 {allowed:.1f}）")
            sys.exit(1)
        print(f"✓ {key} = {value}，上限 {limit}")
        if value < limit and args.update:
            ceilings[key] = value
            print(f"  上限下调到 {value}")

    with open(args.ceilings, "w", encoding="utf-8") as fh:
        json.dump(ceilings, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
