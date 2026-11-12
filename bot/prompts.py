"""两个版本的 system prompt。

v1 = 朴素实现：一段话 + 商品数据，没有任何规则（对照组）
v2 = 完整实现：先路由选 Skill，再只加载被选中 Skill 的正文（实验组）

对照实验的意义：**只改设计，不改模型**。
如果 v2 比 v1 好，说明提升来自策略设计，而不是换了个更强的模型。
"""
from __future__ import annotations

# ============================================================
# v1：朴素 Prompt —— 大多数团队的第一版长这样
# ============================================================

V1_SYSTEM = """你是一个东南亚跨境电商平台的 AI 客服助手。
请根据商品信息，回答用户关于尺码的问题，简洁、友好地给出建议。

【商品数据】
{products_text}
"""


# ============================================================
# v2 第一步：路由
# ============================================================

V2_ROUTER_SYSTEM = """你是电商导购对话的路由器。你的职责只有一个：判断当前这一轮该用哪些 Skill。
你不要回答用户的问题，只输出路由结果。

【可用 Skill】
- clarify-missing-info：检查身高、体重、商品是否齐全，缺失时发起澄清提问
- lookup-size-chart：从结构化数据读取商品尺码表（回答任何尺寸问题前必须先调用）
- recommend-size：基于尺码表 + 用户数据给出具体尺码建议
- remember-preference：把用户的身高体重、版型偏好、尺码反馈写入长期记忆
- escalate-to-human：商品无尺码表 / 用户强烈负面情绪 / 用户要求人工 / 涉及售后责任判定时，转接人工

【调度规则】
1. 只要用户在问尺寸、合身度、版型 —— 必须包含 lookup-size-chart
2. 只要用户消息里出现了身高、体重、版型偏好、尺码反馈 —— 必须包含 remember-preference
3. 商品尺码表为空 —— 必须包含 escalate-to-human，且不要包含 recommend-size
4. 用户强烈负面情绪、要求人工、涉及售后责任 —— 必须包含 escalate-to-human
5. 信息不全会导致无法回答 —— 包含 clarify-missing-info

【已知信息】
{memory_text}

【当前商品】
{product_text}

只输出 JSON，不要任何解释：
{{"skills": ["..."], "missing_slots": ["..."], "escalate": false, "escalate_reason": null}}
"""


# ============================================================
# v2 第二步：执行
# ============================================================

# ------------------------------------------------------------
# 回复语言策略
# ------------------------------------------------------------
#
# 背景：v2/v3 的第一行写死了「请用中文回答用户」。
# 但这是给东南亚市场做的产品——菲律宾用户用 Taglish、印尼用户用 Bahasa，
# 回一段中文等于对话直接终止。
#
# 实测（evals/runs/multilingual/）：同样 6 条 Taglish/印尼语用例
#   v1（无语言指令）→ 6/6 用用户的语言回复
#   v3（"请用中文"）→ 1/6
# 说明**模型本来就会跟随用户语言**，是我们用一句话把它禁掉了。
#
# 所以这里把它变成策略而不是常量：
#   "auto" = 跟随用户语言（产品默认，正确行为）
#   "zh"   = 永远中文（历史行为，用于复现 v1/v2/v3 的冻结结果）
#
# 生产环境还应该有第三层：locale 兜底（用户只说"ok"时用该市场默认语言）。
# 本项目没有 locale 字段，暂时退到英语。
REPLY_LANGUAGE = "auto"

_LANG_RULE_ZH = "请用中文回答用户。"

_LANG_RULE_AUTO = (
    "请用**用户使用的语言**回答，包括混用的情况"
    "（用户写 Taglish 就用 Taglish 回，写印尼语就用印尼语回）。"
    "如果无法判断用户使用什么语言，用英语。"
)

# 模板里的 {lang_rule} 在生成时替换掉，其余 {memory_text} 等占位符留给 .format()
_V2_EXEC_TEMPLATE = """你是东南亚跨境电商平台的 AI 导购助手。{lang_rule}

【身份要求】
你是 AI，不要假装人类。如果被问到，直接说明。

【用户记忆】
{memory_text}

【当前商品数据】
{product_text}

【本轮已加载的 Skill】
{skills_text}

【通用规则】
1. 商品尺寸只能来自上面的商品数据。数据里没有的数字，一个字都不能编。
2. 用户没有提供的个人信息（身高、体重），不要自己假设。
3. 给出尺码建议时，必须说明依据（哪个围度、多少厘米）和风险（估算可能不准）。
4. 如果 Skill 指示需要转人工，要在回复里明确说明原因，并告知已把上下文转过去。
5. 回答简洁，不要超过 200 字，不要夹带商品推销。
"""


def exec_system() -> str:
    """按当前语言策略生成执行段 system prompt。

    为什么是函数而不是常量：
    语言是可配置的策略，常量在模块导入时就定死了，
    命令行参数（--reply-language）就没法改变它。
    """
    rule = _LANG_RULE_ZH if REPLY_LANGUAGE == "zh" else _LANG_RULE_AUTO
    return _V2_EXEC_TEMPLATE.replace("{lang_rule}", rule)


def set_reply_language(lang: str) -> None:
    """切换语言策略。run.py 在开始跑之前调用。"""
    global REPLY_LANGUAGE
    if lang not in ("auto", "zh"):
        raise ValueError(f"不支持的语言策略：{lang}")
    REPLY_LANGUAGE = lang


# ============================================================
# 辅助：把商品数据渲染成 prompt 里的文字
# ============================================================

def render_product(product: dict | None) -> str:
    """把单个商品渲染成易读文本。

    刻意保留 notes 字段 —— 它经常包含与直觉相反的说明
    （"版型偏大，建议选小一码"），是 BC-01 类错误的直接来源。
    """
    if not product:
        return "（未指定商品）"

    lines = [
        f"商品 ID：{product['id']}",
        f"名称：{product['name']}（{product.get('brand', '')}）",
        f"品类：{product.get('category')}",
        f"版型：{product.get('fit_type')}",
        f"弹力：{product.get('stretch')}",
        f"面料：{product.get('material')}",
    ]

    chart = product.get("size_chart")
    if not chart:
        reason = product.get("chart_missing_reason", "未说明")
        lines.append(f"尺码表：**没有**（原因：{reason}）")
    else:
        lines.append(f"尺码表（单位 {chart.get('unit')}，列为 {', '.join(chart.get('columns', []))}）：")
        for size, values in chart.get("rows", {}).items():
            joined = ", ".join(str(v) for v in values)
            lines.append(f"  {size}: {joined}")

    if product.get("notes"):
        lines.append(f"卖家备注：{product['notes']}")

    return "\n".join(lines)


def render_skills(skill_docs: dict[str, str]) -> str:
    """把已加载的 Skill 正文拼起来。

    skill_docs 只包含被路由选中的 Skill —— 这就是渐进式披露：
    没被选中的 Skill 正文根本不进上下文。
    """
    if not skill_docs:
        return "（本轮未加载额外 Skill，按通用规则回答）"

    blocks = []
    for name, doc in skill_docs.items():
        blocks.append(f"=== Skill: {name} ===\n{doc.strip()}")
    return "\n\n".join(blocks)
