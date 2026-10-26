# -*- coding: utf-8 -*-
"""「一句」摘要群的壓縮邏輯。"""
import brief


def test_takes_first_sentence_only():
    text = "男人三四十岁的顶级状态是身材。第二句不该出现。"
    assert brief.to_one_sentence(text) == "男人三四十岁的顶级状态是身材"


def test_handles_newline_and_fullwidth_punctuation():
    assert brief.to_one_sentence("第一行\n第二行") == "第一行"
    assert brief.to_one_sentence("先问是不是？再问为什么") == "先问是不是"


def test_truncates_long_sentence():
    out = brief.to_one_sentence("啊" * 100)
    assert len(out) <= 61 and out.endswith("…")


def test_empty_text_returns_empty():
    assert brief.to_one_sentence("") == ""
    assert brief.to_one_sentence("   \n ") == ""
