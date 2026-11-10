"""评测运行器。

用法：
    python -m evals.run --version v1 --output evals/runs/v1
    python -m evals.run --version v2 --output evals/runs/v2
    python -m evals.run --version v2 --judge --output evals/runs/v2-judged

设计原则（延续自 fin-analyzer evals）：
1. 保存原始响应，不保存"处理后的结论"
2. 自动检查只做形式判断，不冒充质量评分
3. LLM 裁判的分数单独标注，不与人工分混用
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# 允许直接以脚本方式运行（python evals/run.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.agent import (  # noqa: E402
    DEFAULT_MODEL,
    chat,
    get_client,
    load_products,
    v1_turn,
    v2_turn,
)
from bot.memory import UserMemory  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = Path(__file__).resolve().parent / "cases.json"


# ------------------------------------------------------------
# 自动检查：只做形式判断
# ------------------------------------------------------------

_ASK_MARKERS = ("？", "?", "请问", "多少", "方便告诉", "能不能告诉")
_ESCALATE_MARKERS = ("人工", "转接", "客服同事", "专员")
_SIZE_RE = re.compile(r"\b(XXS|XS|S|M|L|XL|XXL|\d{2})\s*码")
_HUMAN_PRETEND = ("我是真人", "我是人工客服", "我是一个人")


def check_case(case: dict, result: dict, product: dict | None) -> list[dict]:
    """按 expect 字段做形式检查。

    返回形如 [{"name": ..., "passed": bool, "detail": ...}, ...]。
    **这些检查不等于质量评分**，只用于快速定位明显问题。
    """
    expect = case.get("expect", {})
    reply = result["turns"][-1]["reply"] if result.get("turns") else ""
    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    # 是否在提问（结尾或中间出现疑问标记）
    asked = any(m in reply for m in _ASK_MARKERS)
    if "should_ask" in expect:
        add("澄清行为", asked == expect["should_ask"], f"模型{'提问了' if asked else '未提问'}")

    # 是否转人工
    escalated = bool(result["turns"][-1].get("escalate")) or any(m in reply for m in _ESCALATE_MARKERS)
    if "should_escalate" in expect:
        add("转人工", escalated == expect["should_escalate"], f"转接信号={'有' if escalated else '无'}")

    # 是否给出预期尺码
    if expect.get("expected_size"):
        target = str(expect["expected_size"])
        add("尺码命中", target in reply, f"期望 {target}，回复中{'出现' if target in reply else '未出现'}")

    # 无尺码表时是否编造具体码
    if expect.get("must_not_fabricate_size") and product is not None:
        has_chart = bool(product.get("size_chart"))
        mentions_size = bool(_SIZE_RE.search(reply))
        add("无编造尺码", has_chart or not mentions_size,
            "商品无尺码表" + ("，回复未给具体码" if not mentions_size else "，但回复给了具体码"))

    # 是否假扮人类
    if expect.get("must_not_pretend_human"):
        faked = any(p in reply for p in _HUMAN_PRETEND)
        add("不假扮人类", not faked, "检测到冒充表述" if faked else "未检测到冒充")

    # 是否引用具体厘米数（说明真的查了表）
    if expect.get("must_cite_cm"):
        add("引用具体数据", bool(re.search(r"\d{2,3}\s*(cm|厘米)", reply)), "回复中" + ("有" if re.search(r"\d{2,3}\s*(cm|厘米)", reply) else "无") + "具体厘米数")

    # 是否识别到数据缺失
    if expect.get("must_say_data_missing"):
        said = any(k in reply for k in ("没有", "未提供", "查不到", "无法确认", "不确定"))
        add("说明数据缺失", said)

    if expect.get("must_use_memory"):
        add("使用记忆", not any(k in reply for k in ("请问您身高", "您身高多少", "身高体重多少")))

    if expect.get("must_mention_update"):
        said = any(k in reply for k in ("更新", "改成", "按 65", "按65"))
        add("提示记忆更新", said)

    # --- v3 新增的三项检查，对应 REPORT.md 里记录的三处缺陷 ---

    # 缺陷 3：卖家备注有强指引（按腰围最准）且估算基准刚变化时，应该问腰围
    if expect.get("must_ask_waist"):
        add("追问腰围", "腰围" in reply, "回复中" + ("提到腰围" if "腰围" in reply else "未提腰围"))

    # 缺陷 4：估算值必须标注，不能写成实测值
    if expect.get("must_label_estimate"):
        labeled = bool(re.search(r"约|大概|估算|区间|左右|粗算", reply))
        add("估算标注", labeled, "含不确定性标注" if labeled else "写成了确定值")

    # 缺陷 1：用户明确要求人工时，不能再追问
    if expect.get("must_not_ask_before_escalate"):
        has_question = any(m in reply for m in ("？", "?"))
        add("转接前不追问", not has_question, "回复里有问句" if has_question else "无追问")

    return checks


# ------------------------------------------------------------
# LLM 裁判（可选）
# ------------------------------------------------------------

JUDGE_SYSTEM = """你是对话质量评审员。请严格按下面的 rubric 给这次电商导购对话打分。

【评分维度（每维 0-2 分）】
1. 事实准确：是否只使用商品数据里存在的数字；有无编造；有无忽略卖家备注
2. 澄清充分：信息不足时是否提问；是否一次只问最关键的一个；用户拒绝时是否能降级
3. 记忆使用：是否用上已知信息；新旧冲突是否用新的并告知
4. 边界遵守：该转人工是否转；转接是否说明原因；有无假装人类
5. 表达共情：有无情绪识别；是否机械；有无夹带推销

【要求】
- 不要因为"答案碰巧对了"给高分，要看过程是否有依据
- 只输出 JSON，格式：{"scores": {"事实准确": 0, "澄清充分": 0, "记忆使用": 0, "边界遵守": 0, "表达共情": 0}, "reasons": {"维度名": "理由"}, "critical_issues": ["..."]}
"""


def judge_case(client, case: dict, result: dict, product: dict | None) -> dict:
    """用另一个模型按 rubric 初评。

    ⚠️ 结果必须标注为"助手初评"，不能与人工评审混用。

    ⚠️ 必须把"对话开始前已有的用户记忆"一起交给裁判。
    否则裁判会认为模型在对话里没用过"165cm"却说出 165cm，判成编造用户信息。
    这是本项目第一轮评测里真实踩到的坑（见 REPORT.md 的"实验设计缺陷"）。
    """
    transcript = "\n".join(
        f"用户：{t['user']}\n助手：{t['reply']}" for t in result["turns"]
    )
    product_text = json.dumps(product, ensure_ascii=False, indent=2) if product else "（无指定商品）"

    # 对话开始前的记忆：裁判不知道这部分，就会把"合理使用记忆"误判为"编造"
    memory_ctx = case.get("memory") or {}
    if memory_ctx:
        memory_text = json.dumps(memory_ctx, ensure_ascii=False, indent=2)
    else:
        memory_text = "（无，用户此前没有任何历史信息）"

    prompt = (
        f"【用例】{case['id']} {case['title']}\n"
        f"【商品数据】\n{product_text}\n\n"
        f"【对话开始前已掌握的用户记忆】\n{memory_text}\n"
        f"注意：如果助手使用了上面已经存在的身高、体重或历史反馈，属于**正确使用记忆**，不是编造。\n\n"
        f"【对话】\n{transcript}\n\n"
        f"【期望】{json.dumps(case.get('expect', {}), ensure_ascii=False)}"
    )

    raw = chat(
        client,
        [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0,
    )

    # 复用 agent 里的 JSON 抽取逻辑
    from bot.agent import _parse_json  # 局部导入，避免污染公开接口

    data = _parse_json(raw)
    scores = data.get("scores") or {}
    total = sum(v for v in scores.values() if isinstance(v, (int, float)))
    return {
        "source": "assistant_judge",  # 明确标注，不是人工评审
        "scores": scores,
        "total": total,
        "reasons": data.get("reasons") or {},
        "critical_issues": data.get("critical_issues") or [],
        "raw": raw,
    }


# ------------------------------------------------------------
# 运行
# ------------------------------------------------------------

def run_case(
    client,
    case: dict,
    products: dict,
    version: str,
    skills_dir: Path | None = None,
) -> dict:
    """跑完一条用例的全部轮次，保存每一轮的原始回复。

    v1 = 朴素基线；v2 / v3 都走完整配置，差别只在 skills_dir 指向哪一版定义。
    """
    product = products.get(case["product_id"]) if case.get("product_id") else None
    memory = UserMemory.from_dict(case.get("memory"), user_id=case["id"])

    history: list[dict] = []
    turns: list[dict] = []

    for msg in case["turns"]:
        if version == "v1":
            res = v1_turn(client, msg, product, history)
        else:
            res = v2_turn(client, msg, product, memory, history, skills_dir=skills_dir)

        turns.append({"user": msg, **res})
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": res["reply"]})

    return {
        "case_id": case["id"],
        "category": case["category"],
        "title": case["title"],
        "failure_mode": case.get("failure_mode"),
        "turns": turns,
        "memory_after": memory.to_dict(),
    }


def build_report(version: str, results: list[dict], judged: bool) -> str:
    """生成 Markdown 报告。"""
    lines = [
        f"# 评测报告 · {version}",
        "",
        f"- 运行时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 模型：{DEFAULT_MODEL}",
        f"- 用例数：{len(results)}",
        f"- 评分来源：{'自动检查 + LLM 裁判初评' if judged else '仅自动检查'}",
        "",
        "> 自动检查只判断**形式特征**（有没有提问、有没有给具体码），"
        "**不等于质量评分**。质量分见 rubric.md 的人工/裁判评审。",
        "",
        "## 自动检查汇总",
        "",
        "| 用例 | 类别 | 检查项 | 通过 | 失败项 |",
        "|---|---|---|---|---|",
    ]

    total_checks = total_passed = 0
    for r in results:
        checks = r.get("checks", [])
        passed = sum(1 for c in checks if c["passed"])
        failed = [c["name"] for c in checks if not c["passed"]]
        total_checks += len(checks)
        total_passed += passed
        lines.append(
            f"| {r['case_id']} | {r['category']} | {len(checks)} | {passed} | "
            f"{', '.join(failed) if failed else '—'} |"
        )

    lines += [
        "",
        f"**自动检查通过率：{total_passed}/{total_checks}**"
        f"（{total_passed / total_checks * 100:.1f}%）" if total_checks else "",
        "",
    ]

    if judged:
        lines += ["## LLM 裁判初评（⛔ 不是人工评审，不能计入人工分数）", ""]
        lines += ["| 用例 | 事实 | 澄清 | 记忆 | 边界 | 共情 | 总分 |", "|---|---|---|---|---|---|---|"]
        totals = []
        for r in results:
            j = r.get("judge") or {}
            s = j.get("scores", {})
            vals = [s.get(k, "-") for k in ("事实准确", "澄清充分", "记忆使用", "边界遵守", "表达共情")]
            if isinstance(j.get("total"), (int, float)):
                totals.append(j["total"])
            lines.append(f"| {r['case_id']} | " + " | ".join(str(v) for v in vals) + f" | {j.get('total', '-')} |")
        if totals:
            lines += ["", f"**平均分：{sum(totals) / len(totals):.2f} / 10**（n={len(totals)}）", ""]

    lines += ["## 逐条原始回复", ""]
    for r in results:
        lines.append(f"### {r['case_id']} · {r['title']}")
        lines.append(f"- 类别：{r['category']} ｜ 失败模式：{r['failure_mode']}")
        for i, t in enumerate(r["turns"], 1):
            lines.append(f"- **第 {i} 轮 · 用户**：{t['user']}")
            lines.append(f"- **第 {i} 轮 · 助手**：{t['reply']}")
            if t.get("skills"):
                lines.append(f"  - 路由到：{', '.join(t['skills'])}")
            if t.get("memory_writes"):
                lines.append(f"  - 记忆写入：{', '.join(t['memory_writes'])}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行电商导购 chatbot 评测")
    parser.add_argument("--version", choices=["v1", "v2", "v3"], required=True)
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--case-ids", default=None, help="逗号分隔的用例 ID，如 A01,B02")
    parser.add_argument("--judge", action="store_true", help="启用 LLM 裁判初评（更慢更贵）")
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="指定 Skill 定义目录；v2 可指向冻结快照 skills_v2_snapshot/",
    )
    args = parser.parse_args()

    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]

    if args.case_ids:
        wanted = {c.strip() for c in args.case_ids.split(",") if c.strip()}
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            raise SystemExit(f"没有匹配的用例：{args.case_ids}")
    elif args.limit:
        cases = cases[: args.limit]

    products = load_products()
    client = get_client()

    out_dir = Path(args.output)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    skills_dir = Path(args.skills_dir) if args.skills_dir else None
    if skills_dir and not skills_dir.is_absolute():
        skills_dir = ROOT / skills_dir

    results: list[dict] = []
    for idx, case in enumerate(cases, 1):
        print(f"[{idx}/{len(cases)}] {case['id']} {case['title']} ...", flush=True)
        result = run_case(client, case, products, args.version, skills_dir)
        product = products.get(case["product_id"]) if case.get("product_id") else None
        result["checks"] = check_case(case, result, product)
        if args.judge:
            result["judge"] = judge_case(client, case, result, product)
        results.append(result)

    # 保存原始结果与快照
    # dataset_sha256 用来区分"结果是不是同一版用例跑出来的"——
    # 改过用例后旧结果就不能直接比较，这个哈希是唯一的凭据。
    import hashlib

    dataset_sha = hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()[:16]

    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "version": args.version,
                "model": DEFAULT_MODEL,
                "run_at": datetime.now().isoformat(timespec="seconds"),
                "dataset_sha256": dataset_sha,
                "skills_dir": str(skills_dir) if skills_dir else "skills/",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(build_report(args.version, results, args.judge), encoding="utf-8")

    print(f"\n完成：{out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
