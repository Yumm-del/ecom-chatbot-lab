"""槽位抽取的回归测试。

为什么要单独测这一层？
因为槽位抽取是**记忆层的入口**：这里抽错一个数，会被写进长期记忆，
然后污染之后所有对话。模型答错一次你可以重跑，记忆写错一次用户要自己发现。

运行：python -m unittest discover tests
或者：python tests/test_slots.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 允许直接运行本文件（python tests/test_slots.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.agent import detect_fit_feedback, extract_slots  # noqa: E402


class TestHeight(unittest.TestCase):
    def test_cm_with_keyword(self):
        self.assertEqual(extract_slots("我身高165cm"), {"height_cm": 165})

    def test_cm_without_keyword(self):
        # 中文口头常见写法：只说"我165"
        self.assertEqual(extract_slots("165cm 60kg").get("height_cm"), 165)

    def test_meter_notation(self):
        # "1.65m" 要还原成 165
        self.assertEqual(extract_slots("身高1.65m").get("height_cm"), 165)

    def test_out_of_range_ignored(self):
        # 明显不是身高（比如年份），不应该被写进记忆
        self.assertNotIn("height_cm", extract_slots("我 199 年出生的"))


class TestWeightUnits(unittest.TestCase):
    """这一组全是真实 bug 的回归用例。"""

    def test_jin_converted_to_kg(self):
        # 120 斤 = 60 kg
        self.assertEqual(extract_slots("我体重120斤").get("weight_kg"), 60.0)

    def test_gongjin_not_halved(self):
        # "公斤" 里含"斤"字，但它是公斤，不能再除以 2
        self.assertEqual(extract_slots("我体重60公斤").get("weight_kg"), 60.0)

    def test_gongjin_with_space(self):
        # 带空格也必须正确，否则 55 会被算成 27.5 并因为低于下限被丢弃
        self.assertEqual(extract_slots("我 55 公斤，这件衬衫买多大？").get("weight_kg"), 55.0)

    def test_kg_english(self):
        self.assertEqual(extract_slots("berat saya 65 kg").get("weight_kg"), 65.0)

    def test_out_of_range_ignored(self):
        self.assertNotIn("weight_kg", extract_slots("我体重 500 kg"))


class TestMultilingual(unittest.TestCase):
    """多语言用例暴露的边界——这里记录的是**当前能力**，不是期望能力。"""

    def test_indonesian_with_unit(self):
        # 带单位能抽到，说明通用数字+单位模式是有效的
        self.assertEqual(
            extract_slots("Beratku sekarang 65 kg, naik dari sebelumnya").get("weight_kg"),
            65.0,
        )

    def test_indonesian_relative_change_fails(self):
        # 已知局限：只认绝对值，不认"胖了 5 公斤"这种相对变化
        self.assertEqual(extract_slots("Beratku naik 5 kilo nih"), {})

    def test_no_unit_fails(self):
        # 已知局限：印尼语"Tinggi aku 165, berat 60"抽不到（没有单位词）
        self.assertEqual(extract_slots("Tinggi aku 165, berat 60"), {})


class TestFitFeedback(unittest.TestCase):
    def test_too_tight(self):
        self.assertEqual(detect_fit_feedback("M 码太紧了"), "too_tight")

    def test_just_right(self):
        self.assertEqual(detect_fit_feedback("这件刚刚好"), "just_right")

    def test_ambiguous_returns_none(self):
        # "还行" 这种模糊表达不能当成结论写进 L3
        self.assertIsNone(detect_fit_feedback("还行吧"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
