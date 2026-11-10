"""对已保存的运行结果做 LLM 裁判初评。

为什么单独写一个脚本，而不是在 run.py 里加 --judge？
1. 评分必须能对**已有的原始回复**复跑，不受对话重新生成的影响
2. 裁判模型和被测模型分开调用，便于记录版本
3. 省 token：不用把整轮对话再跑一遍

用法：
    python -m evals.judge_saved --input evals/runs/v1
    python -m evals.judge_saved --input evals/runs/v2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.agent import DEFAULT_MODEL, chat, get_client, load_products  # noqa: E402
from evals.run import CASES_PATH, judge_case  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="对已保存结果做裁判初评")
    parser.add_argument("--input", required=True, help="包含 results.json 的运行目录")
    args = parser.parse_args()

    in_dir = Path(args.input)
    data = json.loads((in_dir / "results.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]}
    products = load_products()
    client = get_client()

    total = 0.0
    scored = 0
    per_dim: dict[str, list[float]] = {}

    for r in data["results"]:
        case = cases[r["case_id"]]
        product = products.get(case["product_id"]) if case.get("product_id") else None
        print(f"评审 {r['case_id']} ...", flush=True)
        j = judge_case(client, case, r, product)
        r["judge"] = j
        if isinstance(j.get("total"), (int, float)):
            total += j["total"]
            scored += 1
        for dim, val in (j.get("scores") or {}).items():
            if isinstance(val, (int, float)):
                per_dim.setdefault(dim, []).append(val)

    data["judge_meta"] = {
        "model": DEFAULT_MODEL,
        "source": "assistant_judge",
        "note": "助手初评，不是人工评审，不能计入人工一致性",
    }
    (in_dir / "results_judged.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# LLM 裁判初评（⛔ 不是人工评审）",
        "",
        f"- 输入：{in_dir}",
        f"- 被测模型 / 裁判模型：{DEFAULT_MODEL}",
        f"- 已评分用例：{scored}",
        f"- **平均分：{total / scored:.2f} / 10**" if scored else "",
        "",
        "| 用例 | 事实准确 | 澄清充分 | 记忆使用 | 边界遵守 | 表达共情 | 总分 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in data["results"]:
        s = (r.get("judge") or {}).get("scores", {})
        row = [str(s.get(k, "-")) for k in ("事实准确", "澄清充分", "记忆使用", "边界遵守", "表达共情")]
        lines.append(f"| {r['case_id']} | {' | '.join(row)} | {(r.get('judge') or {}).get('total', '-')} |")

    lines += ["", "## 分维度均值", "", "| 维度 | 均值 |", "|---|---|"]
    for dim, vals in per_dim.items():
        lines.append(f"| {dim} | {sum(vals) / len(vals):.2f} |")

    (in_dir / "judge.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n完成：{in_dir / 'judge.md'}")


if __name__ == "__main__":
    main()
