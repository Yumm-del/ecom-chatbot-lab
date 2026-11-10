"""横向对比多个运行目录。

用途：跑完 v1 / v2 / v3 后，一条命令生成对比表，不用手工翻 report.md。

用法：
    python -m evals.compare evals/runs/v1 evals/runs/v2 evals/runs/v3

约定：
- 每个目录下需要有 results.json；有 results_judged.json 时优先用它
- 只有 dataset_sha256 相同的运行才应该被放在一起比较（脚本会检查并警告）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DIMENSIONS = ("事实准确", "澄清充分", "记忆使用", "边界遵守", "表达共情")


def load_run(run_dir: Path) -> dict:
    """优先读裁判后的结果，没有就退回归档结果。"""
    judged = run_dir / "results_judged.json"
    plain = run_dir / "results.json"
    path = judged if judged.exists() else plain
    if not path.exists():
        raise SystemExit(f"{run_dir} 下没有 results.json")
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(run: dict) -> dict:
    """把一次运行压成可以横向比较的几个数。"""
    totals: list[float] = []
    per_dim: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    per_cat: dict[str, list[float]] = {}

    auto_pass = auto_total = 0

    for r in run["results"]:
        checks = r.get("checks") or []
        auto_total += len(checks)
        auto_pass += sum(1 for c in checks if c["passed"])

        judge = r.get("judge") or {}
        scores = judge.get("scores") or {}
        if isinstance(judge.get("total"), (int, float)):
            totals.append(judge["total"])
            per_cat.setdefault(r["category"], []).append(judge["total"])
        for d in DIMENSIONS:
            if isinstance(scores.get(d), (int, float)):
                per_dim[d].append(scores[d])

    return {
        "version": run.get("version", "?"),
        "dataset": run.get("dataset_sha256", "?"),
        "avg": sum(totals) / len(totals) if totals else None,
        "per_dim": {d: (sum(v) / len(v) if v else None) for d, v in per_dim.items()},
        "per_cat": {c: sum(v) / len(v) for c, v in per_cat.items()},
        "auto": f"{auto_pass}/{auto_total}",
        "auto_rate": auto_pass / auto_total if auto_total else 0,
    }


def fmt(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="对比多个评测运行")
    parser.add_argument("runs", nargs="+", help="运行目录列表")
    args = parser.parse_args()

    runs = [summarize(load_run(Path(p))) for p in args.runs]

    # 数据集一致性检查：不同版本用例跑出来的结果不能直接比较
    datasets = {r["dataset"] for r in runs}
    if len(datasets) > 1:
        print("⚠️ 警告：这些运行用的不是同一版用例（dataset_sha256 不同），不能直接比较\n")

    labels = [r["version"] for r in runs]
    header = f"{'指标':<12}" + "".join(f"{lab:>10}" for lab in labels)
    print(header)
    print("-" * len(header))

    print(f"{'总分/10':<12}" + "".join(f"{fmt(r['avg']):>10}" for r in runs))
    print(f"{'自动检查':<12}" + "".join(f"{r['auto']:>10}" for r in runs))

    for d in DIMENSIONS:
        print(f"{d:<12}" + "".join(f"{fmt(r['per_dim'][d]):>10}" for r in runs))

    for c in sorted({c for r in runs for c in r["per_cat"]}):
        print(f"{'类别 ' + c:<12}" + "".join(f"{fmt(r['per_cat'].get(c)):>10}" for r in runs))

    print("\n数据集哈希：" + ", ".join(f"{r['version']}={r['dataset']}" for r in runs))


if __name__ == "__main__":
    main()
