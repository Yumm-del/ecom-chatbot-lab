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

V2_EXEC_SYSTEM = """你是东南亚跨境电商平台的 AI 导购助手。请用中文回答用户。

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
