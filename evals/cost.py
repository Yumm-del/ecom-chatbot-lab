"""成本核算：把 results.json 里的 token 用量换算成钱。

用法：
    python -m evals.cost evals/runs/cost/v1 evals/runs/cost/v2 evals/runs/cost/v3
    python -m evals.cost evals/runs/cost/* --price-in 2.0 --price-out 8.0

设计原则：
1. **价格是参数，不是常量。** 模型价格会变，写死会在半年后变成一条假事实。
2. **相对倍数是价格无关的。** v3 是 v1 的几倍成本，换任何价格都成立，
   这是唯一稳定可引用的结论。
3. **场景分析给出假设和算法，不只给结论。** 每个优化杠杆省了多少钱，
   要能让别人按自己的参数重算一遍。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# 默认价格（元 / 百万 tokens）。
# ⚠️ 这是查询时的参考值，**使用前必须核对官方价格页**。
# 好消息是：下面的"相对倍数"不依赖这两个数。
DEFAULT_PRICE_IN = 2.0
DEFAULT_PRICE_OUT = 8.0


def load_run(path: Path) -> dict:
    """读取一个 run 目录下的 results.json。"""
    p = path / "results.json" if path.is_dir() else path
    data = json.loads(p.read_text(encoding="utf-8"))
    data["_path"] = str(p.parent)
    return data


def summarize(run: dict) -> dict:
    """把一个 run 的用量聚合成一行统计。

    优先用 results.json 顶层的 usage_total；老版本结果没有这个字段时，
    回退到遍历每条用例的 usage，保证这个脚本对历史数据也能跑。
    """
    results = run.get("results") or []
    total = run.get("usage_total")
    if not total:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        for r in results:
            u = r.get("usage") or {}
            for k in total:
                total[k] += u.get(k, 0)

    # 分段统计：router / exec / judge
    stages: dict[str, dict] = {}
    for r in results:
        for t in r.get("turns") or []:
            for name, s in (t.get("usage") or {}).get("by_stage", {}).items():
                bucket = stages.setdefault(
                    name, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
                )
                bucket["prompt_tokens"] += s.get("prompt_tokens", 0)
                bucket["completion_tokens"] += s.get("completion_tokens", 0)
                bucket["calls"] += s.get("calls", 0)

    n_cases = len(results)
    n_turns = sum(len(r.get("turns") or []) for r in results)
    return {
        "name": run.get("version", "?"),
        "path": run.get("_path", ""),
        "n_cases": n_cases,
        "n_turns": n_turns,
        "prompt": total.get("prompt_tokens", 0),
        "completion": total.get("completion_tokens", 0),
        "calls": total.get("calls", 0),
        "stages": stages,
        "judge": run.get("usage_judge") or {},
    }


def cost_of(prompt: int, completion: int, price_in: float, price_out: float) -> float:
    """按"元 / 百万 tokens"折算成本。"""
    return (prompt / 1_000_000) * price_in + (completion / 1_000_000) * price_out


def render(sums: list[dict], price_in: float, price_out: float,
           skill_share: float, cache_ratio: float, router_ratio: float) -> str:
    """生成 Markdown 报告。"""
    base = sums[0]
    base_cost = cost_of(base["prompt"], base["completion"], price_in, price_out) or 1e-9

    lines = [
        "# 成本核算",
        "",
        f"- 计价：输入 {price_in} 元/百万 tokens，输出 {price_out} 元/百万 tokens",
        f"- 样本：每个版本 {base['n_cases']} 条用例 / {base['n_turns']} 轮对话",
        "- ⚠️ 价格是参数。**相对倍数是价格无关的**，价格变了结论不变。",
        "",
        "## 1. 总用量",
        "",
        "| 版本 | 用例 | 轮次 | 调用次数 | 输入 tokens | 输出 tokens | 每轮输入 | 成本(元) | 相对基线 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for s in sums:
        c = cost_of(s["prompt"], s["completion"], price_in, price_out)
        per_turn = s["prompt"] / s["n_turns"] if s["n_turns"] else 0
        lines.append(
            f"| {s['name']} | {s['n_cases']} | {s['n_turns']} | {s['calls']} | "
            f"{s['prompt']:,} | {s['completion']:,} | {per_turn:,.0f} | "
            f"{c:.4f} | {c / base_cost:.2f}× |"
        )

    # --- 分段 ---
    lines += ["", "## 2. 成本花在哪一段", "", "| 版本 | 段 | 调用 | 输入 tokens | 输出 tokens | 输入占比 |", "|---|---|---|---|---|---|"]
    for s in sums:
        for stage in ("router", "exec", "judge"):
            st = s["stages"].get(stage)
            if not st:
                continue
            share = st["prompt_tokens"] / s["prompt"] if s["prompt"] else 0
            lines.append(
                f"| {s['name']} | {stage} | {st['calls']} | {st['prompt_tokens']:,} | "
                f"{st['completion_tokens']:,} | {share:.0%} |"
            )

    # --- 价格敏感度 ---
    lines += [
        "",
        "## 3. 价格敏感度（输入:输出 = 1:R 时的成本倍数）",
        "",
        "以 v1 为 1.00。R 越大（输出越贵），两种架构的差距越小——",
        "因为 v1/v2/v3 的输出量差不多，差距全在输入上。",
        "",
        "| R（输出单价/输入单价） | " + " | ".join(s["name"] for s in sums) + " |",
        "|---|" + "---|" * len(sums),
    ]
    for r in (1, 2, 4, 8, 16):
        row = []
        for s in sums:
            c = s["prompt"] + r * s["completion"]  # 以输入单价为单位
            b = base["prompt"] + r * base["completion"]
            row.append(f"{c / b:.2f}×")
        lines.append(f"| {r} | " + " | ".join(row) + " |")

    # --- 优化杠杆 ---
    lines += [
        "",
        "## 4. 三个优化杠杆（假设写在参数里，可重算）",
        "",
        f"- Skill 正文占 exec 输入的比例：**{skill_share:.0%}**（实测，见 COST.md §3）",
        f"- 缓存命中价格是未命中的：**{cache_ratio:.0%}**",
        f"- 路由换小模型后价格是现在的：**{router_ratio:.0%}**",
        "",
        "| 版本 | 现状成本 | ① 路由换规则/小模型 | ② Skill 正文缓存 | ①②叠加 |",
        "|---|---|---|---|---|",
    ]
    for s in sums:
        router = s["stages"].get("router", {}).get("prompt_tokens", 0)
        exec_p = s["stages"].get("exec", {}).get("prompt_tokens", 0)

        now = cost_of(s["prompt"], s["completion"], price_in, price_out)

        # ① 路由段输入按 router_ratio 计价（输出不变）
        saved1 = (router / 1_000_000) * price_in * (1 - router_ratio)
        c1 = now - saved1

        # ② exec 段里 Skill 正文那部分按 cache_ratio 计价
        saved2 = (exec_p * skill_share / 1_000_000) * price_in * (1 - cache_ratio)
        c2 = now - saved2

        c12 = now - saved1 - saved2
        lines.append(
            f"| {s['name']} | {now:.4f} | {c1:.4f}（−{saved1 / now:.0%}） | "
            f"{c2:.4f}（−{saved2 / now:.0%}） | {c12:.4f}（−{(now - c12) / now:.0%}） |"
        )

    # --- 评测本身的成本 ---
    lines += ["", "## 5. 评测本身的成本（容易忘）", "", "| 版本 | 裁判输入 | 裁判输出 | 占对话成本 |", "|---|---|---|---|"]
    for s in sums:
        j = s["judge"]
        if not j:
            continue
        jc = cost_of(j.get("prompt_tokens", 0), j.get("completion_tokens", 0), price_in, price_out)
        now = cost_of(s["prompt"], s["completion"], price_in, price_out)
        lines.append(
            f"| {s['name']} | {j.get('prompt_tokens', 0):,} | {j.get('completion_tokens', 0):,} | "
            f"{jc / now:.0%} |"
        )

    lines += [
        "",
        "> 评测跑一次 25 条用例 × 3 个版本，裁判成本约为对话成本的量级，",
        "> 但对话成本只在评测时发生一次，**线上是每次会话都发生**——",
        "> 所以线上的成本压力全在 §4，不在裁判。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="把 results.json 的 token 用量换算成成本")
    parser.add_argument("runs", nargs="+", help="run 目录（含 results.json）")
    parser.add_argument("--price-in", type=float, default=DEFAULT_PRICE_IN, help="输入价，元/百万 tokens")
    parser.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUT, help="输出价，元/百万 tokens")
    parser.add_argument("--skill-share", type=float, default=0.85,
                        # argparse 的 help 字符串会用 % 做格式化，百分号必须写成 %%
                        help="Skill 正文占 exec 输入 token 的比例"
                             "（默认 0.85；实测区间 78%%–89%%，见 COST.md §3）")
    parser.add_argument("--cache-ratio", type=float, default=0.25,
                        help="缓存命中价 / 未命中价（默认 0.25，按各家缓存折扣的常见量级）")
    parser.add_argument("--router-ratio", type=float, default=0.25,
                        help="路由换小模型后价格 / 现价（默认 0.25）")
    parser.add_argument("--output", default=None, help="输出 Markdown 路径；不给就打印到终端")
    args = parser.parse_args()

    sums = [summarize(load_run(Path(p))) for p in args.runs]
    md = render(sums, args.price_in, args.price_out,
                args.skill_share, args.cache_ratio, args.router_ratio)

    if args.output:
        out = Path(args.output)
        out.write_text(md, encoding="utf-8")
        print(f"已写入 {out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
