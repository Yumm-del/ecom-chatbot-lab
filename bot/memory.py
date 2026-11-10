"""三层用户记忆模型。

设计思路见 DESIGN.md 3.3 节。核心是三件事：
1. 不同信息有不同生命周期（会话内 / 跨会话 / 长期负反馈）
2. 每条记忆都带来源和置信度，不能把推断当事实
3. 冲突时以新信息为准，但要告知用户并征得同意再落库

目的：让对话有"复利"——用户说过一次的事，不重复问。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 允许写入长期记忆的字段白名单。
# 不在这里的字段（如三围、健康状况、支付信息）一律不落库。
LONG_TERM_FIELDS = {
    "height_cm",
    "weight_kg",
    "fit_preference",   # tight | regular | loose
    "budget_sgd",
    "style_preference",
    "usual_size",
}

# 不同来源的初始置信度。用户自述最可信，模型推断最不可信。
SOURCE_CONFIDENCE = {
    "user_stated": 0.9,
    "purchase_history": 0.8,
    "inferred": 0.5,
}


def _now() -> str:
    """返回 ISO 格式的当前时间，用于记录记忆更新时刻。"""
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class MemoryItem:
    """一条记忆。

    为什么需要 source 和 confidence？
    因为"用户说的 165"和"我猜的 165"在后续对话里应该被区别对待。
    前者可以直接用，后者必须先说明"这是估算的"。
    """

    value: Any
    source: str = "user_stated"
    confidence: float = 0.9
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryItem":
        return cls(
            value=data.get("value"),
            source=data.get("source", "user_stated"),
            confidence=float(data.get("confidence", 0.9)),
            updated_at=data.get("updated_at", _now()),
        )


class UserMemory:
    """一个用户的三层记忆。

    L1 会话槽位：本轮对话确认的信息，会话结束即丢弃
    L2 稳定偏好：身高、体重、版型偏好等，跨会话保留
    L3 负反馈历史：'上次买的 M 太紧' 这类含真实结果的记录，权重最高
    """

    def __init__(self, user_id: str = "anonymous") -> None:
        self.user_id = user_id
        self.l1_session: dict[str, MemoryItem] = {}
        self.l2_profile: dict[str, MemoryItem] = {}
        self.l3_history: list[dict] = []

    # ---------- 构造 / 序列化 ----------

    @classmethod
    def from_dict(cls, data: dict | None, user_id: str = "anonymous") -> "UserMemory":
        """从评测用例里声明的初始记忆构造，方便测试可控场景。"""
        mem = cls(user_id=user_id)
        if not data:
            return mem
        for key, item in (data.get("profile") or {}).items():
            mem.l2_profile[key] = MemoryItem.from_dict(item)
        mem.l3_history = copy.deepcopy(data.get("history") or [])
        return mem

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "session": {k: v.to_dict() for k, v in self.l1_session.items()},
            "profile": {k: v.to_dict() for k, v in self.l2_profile.items()},
            "history": copy.deepcopy(self.l3_history),
        }

    # ---------- 读 ----------

    def get(self, key: str) -> MemoryItem | None:
        """按 L1 → L2 的顺序查。会话内新说的优先于历史档案。"""
        return self.l1_session.get(key) or self.l2_profile.get(key)

    def known_slots(self) -> dict[str, Any]:
        """当前已知的槽位，供澄清 Skill 判断还缺什么。"""
        merged = {k: v.value for k, v in self.l2_profile.items()}
        merged.update({k: v.value for k, v in self.l1_session.items()})
        return merged

    # ---------- 写 ----------

    def write(
        self,
        key: str,
        value: Any,
        *,
        layer: str = "L2",
        source: str = "user_stated",
    ) -> dict | None:
        """写入一条记忆，返回冲突提示（如果有）。

        参数：
            key    : 字段名
            value  : 值
            layer  : "L1"（仅本轮）或 "L2"（跨会话）
            source : user_stated / purchase_history / inferred

        返回：
            冲突信息 dict，或 None 表示无冲突。
            冲突时调用方应把 message 讲给用户听，而不是静默覆盖。
        """
        confidence = SOURCE_CONFIDENCE.get(source, 0.5)

        # 试探性地保存：L1 允许任何字段（本轮临时信息），L2 只允许白名单
        target = self.l1_session if layer == "L1" else self.l2_profile
        if layer == "L2" and key not in LONG_TERM_FIELDS:
            return None  # 静默拒绝落库，保护隐私边界

        old = target.get(key) or self.l2_profile.get(key)
        conflict = None

        # 值真的变了才算冲突；用户重复说同样的话不该被打扰
        if old is not None and str(old.value) != str(value):
            conflict = {
                "field": key,
                "old_value": old.value,
                "new_value": value,
                "message": (
                    f"您之前记的是 {old.value}，这次是 {value}，"
                    f"要我更新一下您的档案吗？"
                ),
            }

        target[key] = MemoryItem(value=value, source=source, confidence=confidence)
        return conflict

    def note_fit_feedback(
        self,
        *,
        category: str,
        brand: str,
        size: str,
        feedback: str,
    ) -> None:
        """记录一次真实穿戴反馈到 L3。

        feedback 取值：too_tight / too_loose / just_right

        L3 是权重最高的一层——它是真实结果，不是估算。
        recommend-size Skill 会优先用它，再考虑 BMI 推算。
        """
        self.l3_history.append(
            {
                "category": category,
                "brand": brand,
                "size": size,
                "feedback": feedback,
                "recorded_at": _now(),
            }
        )

    def clear_session(self) -> None:
        """会话结束时清空 L1，L2 / L3 保留。"""
        self.l1_session.clear()

    # ---------- 渲染进 Prompt ----------

    def render_for_prompt(self) -> str:
        """把记忆渲染成给模型看的一段文字。

        为什么不直接塞 JSON？
        因为 JSON 里的 confidence 数值模型未必会用，写成自然语言更稳：
        '身高 165cm（用户自述，可信）'
        """
        lines: list[str] = []

        merged = self.known_slots()
        if merged:
            lines.append("【已知用户信息】")
            for key in sorted(merged):
                item = self.get(key)
                assert item is not None
                trust = {
                    "user_stated": "用户自述，可直接使用",
                    "purchase_history": "来自历史订单",
                    "inferred": "模型推断，使用前需向用户确认",
                }.get(item.source, item.source)
                lines.append(f"- {key}: {item.value}（{trust}）")

        if self.l3_history:
            lines.append("【历史穿戴反馈】（权重最高，优先于估算）")
            for rec in self.l3_history:
                lines.append(
                    f"- {rec.get('brand')} {rec.get('category')} "
                    f"{rec.get('size')} 码：{rec.get('feedback')}"
                )

        if not lines:
            return "（暂无已知用户信息）"
        return "\n".join(lines)
