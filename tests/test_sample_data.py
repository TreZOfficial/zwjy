# -*- coding: utf-8 -*-
"""示例数据（data/sample_words.json）的自检测试。

示例数据是手工录入的，很容易出现「藏文写对了但组件配不上」的错误。
这里用同一个合成器把每条记录的组件重新算一遍，与 tibetan 字段逐条比对，
相当于给示例数据加了一道防错闸门。
"""

from __future__ import annotations

import json

import pytest

import models
from tibetan import WordComponents, compose_word, validate_word


def test_sample_file_is_valid_json():
    from conftest import ROOT

    with open(ROOT / "data" / "sample_words.json", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert payload["format"] == models.BACKUP_FORMAT
    assert payload["count"] == len(payload["words"])


def test_every_sample_entry_composes_to_its_tibetan(sample_words):
    """组件合成结果必须与 tibetan 字段完全一致。"""
    mismatches = []
    for word in sample_words:
        components = WordComponents.from_dict(word["components"])
        composed = compose_word(components, auto_join=word.get("auto_join", True))
        if composed != word["tibetan"]:
            mismatches.append((word["tibetan"], composed))

    assert mismatches == [], f"以下示例数据的组件与藏文对不上：{mismatches}"


def test_every_sample_entry_has_a_root(sample_words):
    for word in sample_words:
        components = WordComponents.from_dict(word["components"])
        assert components.meaningful_syllables(), word["tibetan"]
        for syllable in components.meaningful_syllables():
            assert syllable.root, f"{word['tibetan']} 有音节缺少基字"


def test_sample_entries_are_orthographically_plausible(sample_words):
    """示例数据不应触发任何组合警告。"""
    problems = {}
    for word in sample_words:
        components = WordComponents.from_dict(word["components"])
        issues = validate_word(components)
        if issues:
            problems[word["tibetan"]] = [issue.message for issue in issues]

    assert problems == {}, f"示例数据触发了组合提示：{problems}"


def test_sample_entries_have_meanings_and_tags(sample_words):
    for word in sample_words:
        assert word["meaning"].strip(), f"{word['tibetan']} 缺少释义"
        assert word["tags"], f"{word['tibetan']} 缺少标签"


def test_sample_entries_cover_all_component_kinds(sample_words):
    """示例数据应当覆盖到全部 7 类组件，方便用户对照学习。"""
    used = set()
    for word in sample_words:
        for syllable in WordComponents.from_dict(word["components"]).syllables:
            for name, value in syllable.to_dict().items():
                if value:
                    used.add(name)

    assert used == {
        "prefix", "superscript", "root", "subjoined",
        "vowel", "suffix", "suffix2",
    }


def test_sample_entries_all_end_with_shad(sample_words):
    """示例数据是词汇表，每个词条都应当带词尾 ཤད（།）。"""
    for word in sample_words:
        assert word["tibetan"].endswith("།"), word["tibetan"]
        assert word["components"]["trailing_shad"] is True, word["tibetan"]


@pytest.mark.parametrize("expected", ["བསྒྲུབས", "ཉི་མ", "རྒྱལ", "སེམས", "དྲྭ་བ", "གྲྭ", "ཕྱྭ"])
def test_sample_contains_key_examples(sample_words, expected):
    """按去掉词尾 ། 之后的形式找词，免得标点形式一变测试就红。"""
    found = [word["tibetan"].rstrip("།") for word in sample_words]
    assert expected in found


def test_sample_data_imports_and_reloads(sample_words):
    """走一遍真实的导入流程，确认组件能被完整回填。"""
    stats = models.import_payload({"words": sample_words}, mode="replace")
    assert stats["added"] == len(sample_words)

    words = models.list_words()
    assert len(words) == len(sample_words)
    for word in words:
        assert word.has_components, word.tibetan
        # 回填出来的组件重新合成，仍然等于存下来的藏文
        assert compose_word(word.word_components) == word.tibetan
