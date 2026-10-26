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


def test_skips_markdown_label_line():
    """實際摘要以「💡 **精彩看点**」開頭，不能把它當成內容。"""
    text = "💡 **精彩看点**\n> 男人三四十岁的顶级状态：身材、钱、心态。后面还有内容"
    assert brief.to_one_sentence(text) == "男人三四十岁的顶级状态：身材、钱、心态"


def test_skips_bare_label_without_emoji():
    assert brief.to_one_sentence("精彩看点\n真正的第一句内容在这里") == "真正的第一句内容在这里"


def test_returns_empty_when_only_label():
    assert brief.to_one_sentence("💡 **精彩看点**\n") == ""


def test_label_with_trailing_colon_is_skipped():
    """實際摘要可能寫成「精彩看点：」，結尾冒號要一併去掉才認得。"""
    assert brief.to_one_sentence("精彩看点：\n真正的第一句内容在这里") == "真正的第一句内容在这里"


def test_decimal_number_is_not_treated_as_ordinal():
    """「3.15」不是序號，不能砍成「15」。"""
    assert brief.to_one_sentence("3.15 消费者权益日的影响") == "3.15 消费者权益日的影响"


def test_chinese_ordinal_is_stripped():
    assert brief.to_one_sentence("1、第一句正文内容") == "第一句正文内容"
