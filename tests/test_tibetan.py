# -*- coding: utf-8 -*-
"""藏文音节合成器的单元测试。

前三个用例是任务要求里明确指定的，其余覆盖边界情况。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from tibetan import (
    MAX_SUBJOINED,
    NONE_LABEL,
    PREFIXES,
    PREFIX_ROOT_RULES,
    ROOTS,
    SUBJOINED,
    SUBJOINED_BEFORE_WAZUR,
    SUBJOINED_MAP,
    SUBJOINED_ROOT_RULES,
    SUBJOINED_TAIL_MAP,
    SUFFIX2_SUFFIX_RULES,
    SUFFIX2S,
    SUFFIXES,
    SUPER_ROOT_RULES,
    SUPERSCRIPTS,
    TSHEG,
    VOWELS,
    WAZUR_LETTER,
    WAZUR_SPECIAL_STACKS,
    Syllable,
    WordComponents,
    allowed_components,
    append_trailing_shad,
    strip_trailing_shad,
    compose_syllable,
    compose_word,
    normalize_subjoined,
    preview,
    validate_syllable,
    validate_word,
)

BASE_DIR = Path(__file__).resolve().parent.parent

#: 规则文档。只在本地保留（已加进 .gitignore），不进仓库；
#: 所以下面依赖它的测试要能在文件缺失时跳过。
RULE_DOC = BASE_DIR / "藏文规则.md"


# ---------------------------------------------------------------------------
# 任务要求里指定的三个用例
# ---------------------------------------------------------------------------
def test_root_with_vowel_and_suffix():
    """基字 ཀ + 元音 ི + 后加字 ག => ཀིག"""
    syllable = Syllable(root="ཀ", vowel="ི", suffix="ག")
    assert compose_syllable(syllable) == "ཀིག"


def test_superscript_with_root_and_vowel():
    """上加字 ས + 基字 ཀ + 元音 ུ => སྐུ"""
    syllable = Syllable(superscript="ས", root="ཀ", vowel="ུ")
    assert compose_syllable(syllable) == "སྐུ"


def test_all_seven_components():
    """七个组件全用上：བ + ས + ག + ར + ུ + བ + ས => བསྒྲུབས"""
    syllable = Syllable(
        prefix="བ",
        superscript="ས",
        root="ག",
        subjoined="ར",
        vowel="ུ",
        suffix="བ",
        suffix2="ས",
    )
    assert compose_syllable(syllable) == "བསྒྲུབས"


def test_required_cases_have_no_warnings():
    """上面三个用例都是规范拼写，不应该产生任何提示。"""
    cases = [
        Syllable(root="ཀ", vowel="ི", suffix="ག"),
        Syllable(superscript="ས", root="ཀ", vowel="ུ"),
        Syllable(
            prefix="བ", superscript="ས", root="ག", subjoined="ར",
            vowel="ུ", suffix="བ", suffix2="ས",
        ),
    ]
    for syllable in cases:
        assert validate_syllable(syllable) == [], compose_syllable(syllable)


# ---------------------------------------------------------------------------
# 合成规则
# ---------------------------------------------------------------------------
def test_empty_vowel_means_default_a():
    """元音为空表示默认元音 a，不输出任何符号。"""
    assert compose_syllable(Syllable(root="ཀ")) == "ཀ"
    assert compose_syllable(Syllable(root="ཀ", vowel="")) == "ཀ"
    assert compose_syllable(Syllable(root="ཀ", vowel="ི")) == "ཀི"


def test_base_is_subjoined_only_when_superscript_present():
    """有上加字时基字用下加形式，没有时用正常形式。"""
    assert compose_syllable(Syllable(root="ཀ")) == "ཀ"
    assert compose_syllable(Syllable(superscript="ས", root="ཀ")) == "སྐ"
    # 下加形式确实是独立字符（U+0F90），而不是「正常形式 + 某个符号」
    assert len(compose_syllable(Syllable(superscript="ས", root="ཀ"))) == 2


def test_subjoined_tail_mapping():
    """下加字字段要换算成下加形式。

    只有 4 个：规则文档不认下加字 ཧ（ྷ），它只出现在 གྷ དྷ བྷ ཛྷ
    这几个梵文转写里，现有词库一次都没用到。
    """
    expected = {"ཡ": "ྱ", "ར": "ྲ", "ལ": "ླ", "ཝ": "ྭ"}
    assert SUBJOINED_TAIL_MAP == expected
    for normal, subjoined in expected.items():
        assert compose_syllable(Syllable(root="ཀ", subjoined=normal)) == "ཀ" + subjoined


# ---------------------------------------------------------------------------
# 词尾 ཤད（།）
# ---------------------------------------------------------------------------
def test_shad_constant_is_the_right_character():
    from tibetan import SHAD

    assert SHAD == "།"
    assert ord(SHAD) == 0x0F0D
    import unicodedata

    assert unicodedata.name(SHAD) == "TIBETAN MARK SHAD"


def test_trailing_shad_defaults_on():
    """词汇表里的词头习惯带 །，所以默认打开。

    注意基字用 མ 而不是 ཀ：ཀ / ག 结尾按规则本来就不加 །，
    用它们测不出开关的效果。
    """
    assert WordComponents().trailing_shad is True
    assert compose_word(WordComponents(syllables=[Syllable(root="མ")])) == "མ།"


def test_trailing_shad_follows_the_last_letter():
    """词尾标点由**词尾字母**决定，不是一律加 །。"""
    def word(root, **kwargs):
        return compose_word(WordComponents(syllables=[Syllable(root=root, **kwargs)]))

    assert word("ཀ") == "ཀ"          # ཀ 结尾：不加
    assert word("ག") == "ག"          # ག 结尾：不加
    assert word("ང") == "ང་།"        # ང 结尾：先 ་ 再 །
    assert word("མ") == "མ།"         # 其余：直接 །
    assert word("ས") == "ས།"
    # 后加字是 ག 的也一样
    assert word("ཀ", vowel="ི", suffix="ག") == "ཀིག"
    assert word("ཐ", suffix="ང") == "ཐང་།"


def test_trailing_shad_can_be_turned_off():
    components = WordComponents(syllables=[Syllable(root="མ")], trailing_shad=False)
    assert compose_word(components) == "མ"


def test_append_trailing_shad_is_idempotent_after_stripping():
    """先剥再补，反复保存不会越加越多（ང 结尾尤其要注意）。"""
    for text in ("མ།", "ང་།", "ཀ"):
        once = append_trailing_shad(strip_trailing_shad(text))
        twice = append_trailing_shad(strip_trailing_shad(once))
        assert once == twice, text


def test_shad_goes_after_everything_else():
    """། 必须在后加字、再后加字之后，是整词最后一个字符。"""
    syllable = Syllable(
        prefix="བ", superscript="ས", root="ག", subjoined=["ར"],
        vowel="ུ", suffix="བ", suffix2="ས",
    )
    word = compose_word(WordComponents(syllables=[syllable]))
    assert word == "བསྒྲུབས།"
    assert word.endswith("།")
    assert word[-2] != "།"


def test_shad_only_added_when_there_is_a_word():
    """一个音节都没合成出来时不许只吐出一个标点。"""
    assert compose_word(WordComponents(syllables=[])) == ""
    assert compose_word(WordComponents(syllables=[Syllable()])) == ""


def test_shad_survives_json_roundtrip():
    for flagged in (True, False):
        original = WordComponents(
            syllables=[Syllable(root="མ")], trailing_shad=flagged
        )
        restored = WordComponents.from_json(original.to_json())
        assert restored.trailing_shad is flagged
        assert compose_word(restored) == compose_word(original)


def test_trailing_shad_is_not_a_component():
    """། 是排版开关，不是音节组件——校验时不该因为它报任何错。"""
    word = compose_word(WordComponents(syllables=[Syllable(root="མ")]))
    assert word == "མ།"
    issues = validate_word(WordComponents(syllables=[Syllable(root="མ")]))
    assert issues == []


# ---------------------------------------------------------------------------
# 多层下加字（双下加字）
# ---------------------------------------------------------------------------
def test_two_subjoined_known_stems():
    """已证实的双下加字词形：基字 + ྲ + ྭ 与 基字 + ྱ + ྭ。"""
    assert compose_syllable(Syllable(root="ག", subjoined=["ར", "ཝ"])) == "གྲྭ"
    assert compose_syllable(Syllable(root="ད", subjoined=["ར", "ཝ"])) == "དྲྭ"
    assert compose_syllable(Syllable(root="ཕ", subjoined=["ཡ", "ཝ"])) == "ཕྱྭ"


def test_drwa_ba_the_word_for_net():
    """དྲྭ་བ = 网。第一个音节 དྲྭ 是双下加字，第二个音节是 བ。"""
    components = WordComponents(
        syllables=[
            Syllable(root="ད", subjoined=["ར", "ཝ"]),
            Syllable(root="བ"),
        ]
    )
    # 默认带词尾 ཤད（词汇表里的写法）
    assert compose_word(components) == "དྲྭ་བ།"
    assert [compose_syllable(s) for s in components.syllables] == ["དྲྭ", "བ"]
    assert validate_word(components) == []

    # 关掉后就是不带标点的纯词语
    components.trailing_shad = False
    assert compose_word(components) == "དྲྭ་བ"


def test_two_subjoined_uses_plain_subjoined_forms():
    """第二个下加字用的是普通下加形式，不是 U+0FBA 那组「固定形式」。

    固定形式（ྺ U+0FBA / ྻ U+0FBB / ྼ U+0FBC）只用于梵文转写，
    藏文自身的堆叠用 U+0FAD / U+0FB1 / U+0FB2。
    """
    assert [ord(c) for c in compose_syllable(Syllable(root="ག", subjoined=["ར", "ཝ"]))] == [
        0x0F42, 0x0FB2, 0x0FAD,
    ]
    assert [ord(c) for c in compose_syllable(Syllable(root="ཕ", subjoined=["ཡ", "ཝ"]))] == [
        0x0F55, 0x0FB1, 0x0FAD,
    ]


def test_second_layer_structure_rule():
    """第二层下加字必须是 wa-zur；第一层必须是有据可查的 ྲ 或 ྱ。"""
    assert WAZUR_LETTER == "ཝ"
    assert SUBJOINED_BEFORE_WAZUR == frozenset({"ཡ", "ར"})


def test_known_double_subjoined_produces_no_warnings():
    """有据可查的词形都不应触发提示（不锁死基字，见下条）。"""
    for root in ("ག", "ད"):
        assert validate_syllable(Syllable(root=root, subjoined=["ར", "ཝ"])) == [], root
    assert validate_syllable(Syllable(root="ཕ", subjoined=["ཡ", "ཝ"])) == []


def test_rule_does_not_pin_the_base_consonant():
    """早期版本写死了一份「只有 གྲྭ 与 ཕྱྭ」的白名单，那是错的。

    真实的双下加字不止那两个——དྲྭ་བ（网）就是个常用词，
    白名单会把这么常见的词误报成「极为罕见」。

    所以规则按结构判、**不锁基字**。这么做的代价是：
    像 ཧྲྭ 这种并不存在的拼法也不会被提示（ཧྲྭ 不存在，是从一份
    OCR 错乱的教材扫描件里误读出来的）。这个代价是刻意接受的——
    漏报一个不存在的拼法，比把正确的常用词误报成「罕见」要好得多。
    """
    for root in ("ཀ", "ཁ", "ག", "ཏ", "ཐ", "ད", "ན", "པ", "ཕ", "བ", "མ", "ས", "ཧ"):
        issues = validate_syllable(Syllable(root=root, subjoined=["ར", "ཝ"]))
        # 只要求「双下加字」这条规则不开火；
        # 第一层下加字与基字的搭配是否常见，是另一条规则的事
        assert not any("第二层" in i.message or "wa-zur" in i.message for i in issues), root


def test_second_layer_must_be_wazur():
    issues = validate_syllable(Syllable(root="ག", subjoined=["ར", "ཡ"]))
    assert any(i.level == "warning" and "第二层" in i.message for i in issues)
    # 仍然照常合成
    assert compose_syllable(Syllable(root="ག", subjoined=["ར", "ཡ"])) == "གྲྱ"


def test_wazur_on_an_unattested_first_layer_warns():
    """ླ 之上再叠 wa-zur 没有实际用例（有据可查的只有 ྲ、ྱ 两种）。"""
    issues = validate_syllable(Syllable(root="ག", subjoined=["ལ", "ཝ"]))
    assert any("wa-zur" in i.message for i in issues)


def test_subjoined_ha_is_no_longer_a_component():
    """下加字 ཧ（ྷ）已按规则文档移除，现在应报「不在组件表中」的硬错误。"""
    issues = validate_syllable(Syllable(root="ག", subjoined=["ཧ"]))
    assert any(i.level == "error" and "ཧ" in i.message for i in issues)


def test_subjoined_order_matters_for_encoding():
    """两层下加字的顺序就是编码顺序，不能颠倒。"""
    assert compose_syllable(Syllable(root="ག", subjoined=["ར", "ཝ"])) == "གྲྭ"
    reordered = compose_syllable(Syllable(root="ག", subjoined=["ཝ", "ར"]))
    assert reordered != "གྲྭ"
    assert [ord(c) for c in reordered] == [0x0F42, 0x0FAD, 0x0FB2]


def test_repeated_subjoined_letter_warns():
    issues = validate_syllable(Syllable(root="ག", subjoined=["ར", "ར"]))
    assert any(i.level == "warning" and "重复" in i.message for i in issues)


def test_three_or_more_subjoined_warns():
    syllable = Syllable(root="ག", subjoined=["ར", "ཝ", "ལ"])
    issues = validate_syllable(syllable)
    assert any(i.level == "warning" and "梵文转写" in i.message for i in issues)
    # 三层也能正确合成
    assert compose_syllable(syllable) == "གྲྭླ"


def test_superscript_with_two_subjoined_warns():
    """上加字 + 两层下加字 = 四层堆叠，藏文里没有用例。"""
    syllable = Syllable(superscript="ས", root="ག", subjoined=["ར", "ཝ"])
    assert any("四层堆叠" in i.message for i in validate_syllable(syllable))


def test_unknown_subjoined_letter_is_an_error():
    issues = validate_syllable(Syllable(root="ཀ", subjoined=["ར", "ཞ"]))
    assert any(i.level == "error" and "第 2 层下加字" in i.message for i in issues)


def test_max_subjoined_constant():
    assert MAX_SUBJOINED == 3


def test_two_subjoined_survives_json_roundtrip():
    original = WordComponents(syllables=[Syllable(root="ག", subjoined=["ར", "ཝ"])])
    restored = WordComponents.from_json(original.to_json())
    assert restored.syllables[0].subjoined == ["ར", "ཝ"]
    assert compose_word(restored) == "གྲྭ།"


# ---------------------------------------------------------------------------
# 下加字字段的兼容写法
# ---------------------------------------------------------------------------
def test_subjoined_accepts_plain_string():
    """写法上偷懒：字符串等价于单元素列表。"""
    assert Syllable(root="ག", subjoined="ར").subjoined == ["ར"]
    assert compose_syllable(Syllable(root="ག", subjoined="ར")) == "གྲ"


def test_normalize_subjoined_handles_all_shapes():
    assert normalize_subjoined(None) == []
    assert normalize_subjoined("") == []
    assert normalize_subjoined([]) == []
    assert normalize_subjoined("ར") == ["ར"]
    assert normalize_subjoined(["ར", "ཝ"]) == ["ར", "ཝ"]
    # 过滤掉空串，容忍中间的空位
    assert normalize_subjoined(["", "ར", ""]) == ["ར"]
    assert normalize_subjoined(("ར", "ཝ")) == ["ར", "ཝ"]


# ---------------------------------------------------------------------------
# 藏文文本归一化（间隔符 tsheg / tsheg bstar）
# ---------------------------------------------------------------------------
def test_normalize_text_maps_tsheg_bstar_to_tsheg():
    """U+0F0C 与 U+0F0B 长得一样但码点不同，必须归一，否则搜不到。"""
    from tibetan import TSHEG, TSHEG_BSTAR, normalize_text

    assert TSHEG == "་" and TSHEG_BSTAR == "༌"
    assert normalize_text("དྲྭ༌བ") == "དྲྭ་བ"
    assert [ord(c) for c in normalize_text("དྲྭ༌བ")][3] == 0x0F0B
    # 本来就是标准写法的保持原样
    assert normalize_text("དྲྭ་བ") == "དྲྭ་བ"


def test_normalize_text_is_idempotent_and_handles_empty():
    from tibetan import normalize_text

    once = normalize_text("ཉི༌མ")
    assert normalize_text(once) == once
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_normalize_text_preserves_vowel_signs():
    """NFC 不该动元音符号与下加字。"""
    from tibetan import normalize_text

    for text in ["ཀིག", "སྐུ", "བསྒྲུབས", "དྲྭ་བ", "གྲྭ"]:
        assert normalize_text(text) == text


def test_v1_backup_with_string_subjoined_still_reads():
    """v1 备份里 subjoined 是字符串，必须照常读出并合成。"""
    v1 = {
        "version": 1,
        "auto_join": True,
        "syllables": [
            {"prefix": "བ", "superscript": "ས", "root": "ག", "subjoined": "ར",
             "vowel": "ུ", "suffix": "བ", "suffix2": "ས"},
        ],
    }
    components = WordComponents.from_dict(v1)
    assert components.syllables[0].subjoined == ["ར"]
    # v1 里没有 trailing_shad 字段 -> 用模型默认值（开）。
    # 如果是**整条词条**从备份里导入，models._word_from_backup 会按它原有的
    # 藏文推断，不会凭空多出 །（见 test_models 里对应的用例）。
    assert components.trailing_shad is True
    assert compose_word(components) == "བསྒྲུབས།"


def test_v2_output_always_uses_a_list():
    data = WordComponents(syllables=[Syllable(root="ཀ")]).to_dict()
    assert data["version"] == 2
    assert data["syllables"][0]["subjoined"] == []


def test_subjoined_map_uses_unicode_offset():
    """正常辅音 -> 下加形式 的整表映射（U+0F40 区 -> U+0F90 区）。"""
    assert len(SUBJOINED_MAP) == len(ROOTS) == 30
    for consonant, subjoined in SUBJOINED_MAP.items():
        assert ord(subjoined) == ord(consonant) + 0x50
        assert 0x0F90 <= ord(subjoined) <= 0x0FBC
    # 抽查几个容易写错的
    assert SUBJOINED_MAP["ཀ"] == "ྐ"
    assert SUBJOINED_MAP["ག"] == "ྒ"
    assert SUBJOINED_MAP["ན"] == "ྣ"
    assert SUBJOINED_MAP["ལ"] == "ླ"


def test_vowel_comes_after_the_whole_stack():
    """元音符号追加在基字（或其下加形式）以及下加字之后。"""
    syllable = Syllable(superscript="ར", root="ག", subjoined="ཡ", vowel="ུ")
    assert compose_syllable(syllable) == "རྒྱུ"


def test_components_are_output_in_unicode_stable_order():
    """合成结果天然满足 Unicode 规范顺序，不需要额外 normalize。"""
    for syllable in [
        Syllable(root="ཀ", vowel="ི", suffix="ག"),
        Syllable(superscript="ས", root="ཀ", vowel="ུ"),
        Syllable(prefix="བ", superscript="ས", root="ག", subjoined="ར",
                 vowel="ུ", suffix="བ", suffix2="ས"),
    ]:
        text = compose_syllable(syllable)
        assert unicodedata.is_normalized("NFC", text), text


# ---------------------------------------------------------------------------
# 多音节
# ---------------------------------------------------------------------------
def test_syllables_joined_with_tsheg_by_default():
    # 这条只关心音节之间怎么连，所以显式关掉词尾的 །（它有自己的测试）
    components = WordComponents(
        syllables=[Syllable(root="ཉ", vowel="ི"), Syllable(root="མ")],
        trailing_shad=False,
    )
    assert compose_word(components) == "ཉི་མ"
    assert TSHEG in compose_word(components)


def test_syllables_can_be_joined_directly():
    components = WordComponents(
        syllables=[Syllable(root="ཉ", vowel="ི"), Syllable(root="མ")],
        auto_join=False,
        trailing_shad=False,
    )
    assert compose_word(components) == "ཉིམ"


def test_blank_syllables_are_skipped():
    components = WordComponents(
        syllables=[Syllable(), Syllable(root="མ", vowel="ི"), Syllable()],
        trailing_shad=False,
    )
    assert compose_word(components) == "མི"
    assert validate_word(components) == []


def test_empty_components_compose_to_empty_string():
    assert compose_word(WordComponents()) == ""
    assert compose_word(WordComponents(syllables=[Syllable()])) == ""


# ---------------------------------------------------------------------------
# 校验：错误（无法合成）
# ---------------------------------------------------------------------------
def test_missing_root_is_an_error():
    issues = validate_syllable(Syllable(vowel="ི"))
    assert len(issues) == 1
    assert issues[0].level == "error"
    assert "基字" in issues[0].message
    # 缺基字时合成结果为空，不会静默生成错误的藏文
    assert compose_syllable(Syllable(vowel="ི")) == ""


def test_completely_blank_word_is_an_error():
    issues = validate_word(WordComponents(syllables=[Syllable()]))
    assert [i.level for i in issues] == ["error"]


def test_unknown_component_value_is_an_error():
    issues = validate_syllable(Syllable(root="ཀ", prefix="ཟ"))
    assert any(i.level == "error" and "前加字" in i.message for i in issues)

    issues = validate_syllable(Syllable(root="ཀ", subjoined="ག"))
    assert any(i.level == "error" and "下加字" in i.message for i in issues)

    issues = validate_syllable(Syllable(root="ཀ", vowel="ཱ"))
    assert any(i.level == "error" and "元音" in i.message for i in issues)


def test_suffix2_without_suffix_is_an_error():
    issues = validate_syllable(Syllable(root="ཀ", suffix2="ས"))
    assert any(i.level == "error" and "后加字" in i.message for i in issues)


# ---------------------------------------------------------------------------
# 校验：警告（不常见组合，但不阻止保存）
# ---------------------------------------------------------------------------
def test_uncommon_prefix_root_pair_warns():
    """前加字 ག 不常与基字 མ 搭配。"""
    issues = validate_syllable(Syllable(prefix="ག", root="མ"))
    assert any(i.level == "warning" for i in issues)
    assert not any(i.level == "error" for i in issues)
    # 仍然照常合成，不阻止保存
    assert compose_syllable(Syllable(prefix="ག", root="མ")) == "གམ"


def test_common_prefix_root_pair_does_not_warn():
    """前加字 ད + 基字 མ（如 དམར 红）是常见搭配。"""
    assert validate_syllable(Syllable(prefix="ད", root="མ")) == []


def test_uncommon_superscript_root_pair_warns():
    """上加字 ར 不常与基字 ཤ 搭配。"""
    issues = validate_syllable(Syllable(superscript="ར", root="ཤ"))
    assert any(i.level == "warning" for i in issues)


def test_uncommon_subjoined_root_pair_warns():
    """下加字 ཡ 不常与基字 ད 搭配。"""
    issues = validate_syllable(Syllable(root="ད", subjoined="ཡ"))
    assert any(i.level == "warning" for i in issues)


def test_ra_superscript_takes_ja():
    """རྗ（如 རྗེ 主、རྗེས 之后）是常见搭配，不该报警告。

    早期版本的上加字表漏了 ཇ——十二个 ར་མགོ 里正好少了它，
    于是 རྗེ 这种常用词一录就被误报。
    """
    assert validate_syllable(Syllable(superscript="ར", root="ཇ")) == []
    # 十二个经典 ར་མགོ 全都要通过
    for root in "ཀགངཇཉཏདནབམཙཛ":
        assert validate_syllable(Syllable(superscript="ར", root=root)) == [], root


def test_prefix_tables_cover_the_full_attested_sets():
    """前加字 ག / འ 的搭配表各漏过几个常用基字。

    - ག 漏了 ཙ ཞ ཟ：གཞི（基础）、གཞན（其他）、གཟའ（行星）、གཙོ（主要）
    - འ 漏了 ཆ：འཆར་བ（生起、出现）
    """
    for root in "ཅཉཏདནཙཞཟཤསཡ":          # ག 的十一个
        assert validate_syllable(Syllable(prefix="ག", root=root)) == [], root
    for root in "ཁགཆཇཐདཕབཚཛ":            # འ 的十个
        assert validate_syllable(Syllable(prefix="འ", root=root)) == [], root


def test_same_cluster_gets_the_same_verdict_however_it_is_split():
    """同一串字，不管按哪种拆法录入，结论必须一致。

    藏文里同一个字节序列常常有不止一种拆法，两种拆法写出来**一模一样**：

        ས + ྲ    上加字 ས / 基字 ར    （སྲིད་པ 轮回、སྲོག 命、སྲུང 保护）
        ས + ྲ    基字 ས / 下加字 ྲ

    用户只能挑一种拆法，但这串字算不算藏文跟挑哪种拆法无关。
    早先上加字那一支只看 SUPER_ROOT_RULES，于是同一个词按上加字录会被
    误报「搭配不常见」，按基字 + 下加字录却不报。

    这条测试把两个方向的所有拆法都枚举一遍，按合成出的字节分组，
    要求同组的结论相同——以后再往表里加东西，也不会重新撕开这个口子。
    """

    def cluster(superscript: str = "", root: str = "", subjoined: str = "") -> str:
        """这段组件实际合成出来的字节序列。"""
        text = superscript + SUBJOINED_MAP[root] if superscript else root
        return text + (SUBJOINED_MAP[subjoined] if subjoined else "")

    def verdict(**kwargs) -> bool:
        subjoined = kwargs.get("subjoined") or ""
        syllable = Syllable(
            superscript=kwargs.get("superscript", ""),
            root=kwargs.get("root", ""),
            subjoined=[subjoined] if subjoined else [],
        )
        return not validate_syllable(syllable)

    groups: dict[str, set[bool]] = {}
    for sup in SUPERSCRIPTS:
        for root in ROOTS:
            groups.setdefault(cluster(superscript=sup, root=root), set()).add(
                verdict(superscript=sup, root=root)
            )
    for root in ROOTS:
        for sub in SUBJOINED:
            groups.setdefault(cluster(root=root, subjoined=sub), set()).add(
                verdict(root=root, subjoined=sub)
            )

    inconsistent = {text: v for text, v in groups.items() if len(v) > 1}
    assert not inconsistent, f"同一串字结论不一致：{inconsistent}"


def test_suffix2_after_unusual_suffix_warns():
    """再后加字 ས 通常只跟在 ག / ང / བ / མ 之后。"""
    issues = validate_syllable(Syllable(root="ཀ", suffix="ད", suffix2="ས"))
    assert any(i.level == "warning" for i in issues)

    assert validate_syllable(Syllable(root="ཀ", suffix="ག", suffix2="ས")) == []


def test_a_root_usually_takes_no_affix():
    issues = validate_syllable(Syllable(prefix="བ", root="ཨ"))
    assert any(i.level == "warning" for i in issues)


def test_warnings_never_block_composition():
    """警告级别的组合照样能合成出字符串。"""
    syllable = Syllable(prefix="ག", root="མ", vowel="ོ")
    assert [i.level for i in validate_syllable(syllable)] == ["warning"]
    assert compose_syllable(syllable) == "གམོ"


# ---------------------------------------------------------------------------
# 组件表本身
# ---------------------------------------------------------------------------
def test_component_tables_are_as_specified():
    """七张组件表的内容。

    下加字与再后加字按「藏文规则.md」收窄了：
    下加字去掉 ཧ（ྷ），再后加字只留 ས。
    """
    assert PREFIXES == ("ག", "ད", "བ", "མ", "འ")
    assert SUPERSCRIPTS == ("ར", "ལ", "ས")
    assert len(ROOTS) == 30
    assert SUBJOINED == ("ཡ", "ར", "ལ", "ཝ")
    assert VOWELS == ("ི", "ུ", "ེ", "ོ")
    assert SUFFIXES == ("ག", "ང", "ད", "ན", "བ", "མ", "འ", "ར", "ལ", "ས")
    assert SUFFIX2S == ("ས",)


def test_all_component_options_include_none_except_root():
    from tibetan import build_component_options

    options = build_component_options()
    assert set(options) == {
        "prefix", "superscript", "root", "subjoined", "vowel", "suffix", "suffix2"
    }
    # 基字必选，下拉框里没有「无」
    assert all(item["value"] for item in options["root"])
    assert len(options["root"]) == 30
    # 其余 6 个都有「不选」项（连元音也是——留空就是默认元音 a，
    # 这个说明写在字段名上，不占选项的宽度）
    for key in ("prefix", "superscript", "subjoined", "vowel", "suffix", "suffix2"):
        assert options[key][0]["value"] == "", key
        assert options[key][0]["label"] == NONE_LABEL, key


def test_component_option_labels_stay_short():
    """选项文案必须短——这是防止下拉框被撑破的护栏。

    组件下拉框为了看清藏文用了很大的字号（见 static/style.css 里
    .tibetan-select 的说明）。选项里只要有几个汉字，就会超出下拉框宽度、
    显示成「无（不加前加…」这样被截断的样子。所以每个选项最多一个字。
    """
    from tibetan import build_component_options

    for key, items in build_component_options().items():
        for item in items:
            assert len(item["label"]) == 1, f"{key} 的选项 {item['label']!r} 太长了"


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------
def test_components_json_roundtrip():
    original = WordComponents(
        syllables=[
            Syllable(root="ཉ", vowel="ི"),
            Syllable(prefix="བ", superscript="ས", root="ག", subjoined="ར",
                     vowel="ུ", suffix="བ", suffix2="ས"),
        ],
        auto_join=False,
    )
    restored = WordComponents.from_json(original.to_json())
    assert restored.to_dict() == original.to_dict()
    assert compose_word(restored) == compose_word(original)


def test_from_dict_tolerates_missing_and_null_fields():
    syllable = Syllable.from_dict({"root": "ཀ", "vowel": None, "unknown": "x"})
    assert syllable.root == "ཀ"
    assert syllable.vowel == ""
    assert syllable.prefix == ""
    assert compose_syllable(syllable) == "ཀ"


def test_from_json_tolerates_garbage():
    assert WordComponents.from_json("not json").syllables == []
    assert WordComponents.from_json("").syllables == []
    assert WordComponents.from_json("[1,2,3]").syllables == []
    assert WordComponents.from_json(None).syllables == []


# ---------------------------------------------------------------------------
# 预览接口（前端实时预览用的那份数据）
# ---------------------------------------------------------------------------
def test_preview_returns_word_syllables_and_issues():
    payload = {
        "auto_join": True,
        "syllables": [
            {"root": "ཉ", "vowel": "ི"},
            {"root": "མ"},
        ],
    }
    result = preview(payload)
    assert result["tibetan"] == "ཉི་མ།"      # 默认带词尾 ཤད
    assert result["syllable_strings"] == ["ཉི", "མ"]   # 每个音节的预览不带 །
    assert result["issues"] == []

    payload["trailing_shad"] = False
    assert preview(payload)["tibetan"] == "ཉི་མ"


def test_preview_reports_issues():
    payload = {"auto_join": True, "syllables": [{"vowel": "ི"}]}
    result = preview(payload)
    assert result["tibetan"] == ""
    assert [i["level"] for i in result["issues"]] == ["error"]


def test_preview_carries_allowed_components():
    """录入页靠 preview 顺带拿到「还能选什么」，省一次往返。"""
    payload = {"auto_join": True, "syllables": [{"root": "ཀ"}]}
    result = preview(payload)
    assert "allowed" in result
    assert len(result["allowed"]) == 1
    assert "ལ" in result["allowed"][0]["subjoined"]   # ཀླ 是合法下加字
    assert result["allowed"][0]["superscript"] == ["ར", "ལ", "ས"]


# ---------------------------------------------------------------------------
# 下拉框过滤：allowed_components
# ---------------------------------------------------------------------------
def test_allowed_components_is_unfiltered_before_a_root_is_chosen():
    """还没选基字时，除了再后加字，其余字段一律不做限制。"""
    allowed = allowed_components(Syllable())
    assert allowed["prefix"] == list(PREFIXES)
    assert allowed["superscript"] == list(SUPERSCRIPTS)
    assert allowed["root"] == list(ROOTS)
    assert allowed["subjoined"] == list(SUBJOINED)
    assert allowed["suffix"] == list(SUFFIXES)
    # 再后加字要有后加字才能加，这里还没有
    assert allowed["suffix2"] == []


def test_allowed_roots_follow_the_document_tables():
    """三个上加字各自能带哪些基字，与文档一一对应。

    这条同时钉住了「过滤用严格表」这个设计：文档里没有的基字不出现，
    即使用户换个拆法也能写出同一个字。
    """
    assert set(allowed_components(Syllable(superscript="ར"))["root"]) == \
        set(SUPER_ROOT_RULES["ར"])
    assert set(allowed_components(Syllable(superscript="ལ"))["root"]) == \
        set(SUPER_ROOT_RULES["ལ"])
    assert set(allowed_components(Syllable(superscript="ས"))["root"]) == \
        set(SUPER_ROOT_RULES["ས"])
    assert set(allowed_components(Syllable(prefix="བ"))["root"]) == \
        set(PREFIX_ROOT_RULES["བ"])

    # སླ 不靠「上加字 ས + 基字 ལ」录入——文档的 ས 表里没有 ལ。
    # 界面会引导用户改走「基字 ས + 下加字 ལ」，所以那条路必须是通的。
    assert "ལ" not in allowed_components(Syllable(superscript="ས"))["root"]
    assert "ལ" in allowed_components(Syllable(root="ས"))["subjoined"]


def test_allowed_components_wazur_special_case():
    """文档给 ཝ 开的特例：རྩ 下面可以加 ཝ（རྩྭ 草）。"""
    allowed = allowed_components(Syllable(superscript="ར", root="ཙ"))
    assert allowed["subjoined"] == ["ཝ"]

    # 没有上加字时，基字 ཙ 底下不能单独加 ཝ
    assert allowed_components(Syllable(root="ཙ"))["subjoined"] == []


def test_allowed_components_subjoined_tail_is_wazur_only():
    """第二层下加字按结构只可能是 ་ཝ。"""
    assert allowed_components(Syllable(root="ག", subjoined=["ར"]))["subjoined_tail"] == ["ཝ"]
    # 第一层不是 ཡ / ར 时，第二层没有合法选项
    assert allowed_components(Syllable(root="ག", subjoined=["ལ"]))["subjoined_tail"] == []


def test_allowed_components_suffix2_needs_a_suffix():
    assert allowed_components(Syllable(root="ཀ", suffix="ག"))["suffix2"] == ["ས"]
    assert allowed_components(Syllable(root="ཀ", suffix="ད"))["suffix2"] == []
    assert allowed_components(Syllable(root="ཀ"))["suffix2"] == []


def test_allowed_components_gives_roots_without_affix_nothing():
    """འ / ཨ 是合法基字，但不接受任何前后缀。"""
    allowed = allowed_components(Syllable(root="ཨ"))
    assert allowed["prefix"] == [] and allowed["superscript"] == []
    assert allowed["subjoined"] == []
    assert "ཨ" in allowed_components(Syllable())["root"]   # 本身仍在基字列表里


def test_every_allowed_option_is_warning_free():
    """核心不变式：过滤列出来的值，填进去都不该产生警告。

    过滤（allowed_components）与校验（validate_syllable）是两套调用路径，
    这条测试把它们钉在一起——界面上选得出来的，服务端不能回头说它不常见。
    """
    bases = [
        Syllable(root="ཀ"),
        Syllable(root="ཤ"),
        Syllable(root="ཨ"),
        Syllable(superscript="ར"),
        Syllable(superscript="ས"),
        Syllable(prefix="བ"),
        Syllable(root="ཙ", superscript="ར"),
        Syllable(root="ས"),
        Syllable(root="ལ"),
        Syllable(root="ཀ", subjoined=["ར"]),
        Syllable(root="ཀ", suffix="ག"),
        Syllable(root="ཀ", suffix="ད"),
    ]
    fields = ("prefix", "superscript", "root", "vowel", "suffix", "suffix2")

    for base in bases:
        allowed = allowed_components(base)
        for field in fields:
            if not base.root and field != "root":
                continue          # 没有基字时其余字段无从判定，跳过
            for value in allowed[field]:
                probe = replace(base, **{field: value})
                assert validate_syllable(probe) == [], f"{base} + {field}={value}"
        if base.root:            # 没有基字时单加下加字本来就报错，不是这条要测的
            for value in allowed["subjoined"]:
                if base.subjoined:
                    probe = replace(base, subjoined=[value, *base.subjoined[1:]])
                else:
                    probe = replace(base, subjoined=[value])
                assert validate_syllable(probe) == [], f"{base} + 下加字={value}"
        # 第二层只在已经有第一层时才有意义
        if base.subjoined:
            for value in allowed["subjoined_tail"]:
                probe = replace(base, subjoined=[*base.subjoined, value])
                assert validate_syllable(probe) == [], f"{base} + 第二层={value}"


# ---------------------------------------------------------------------------
# 与「藏文规则.md」的一致性
# ---------------------------------------------------------------------------
def _doc_lists(section: str) -> dict[str, set[str]]:
    """从规则文档里抠出某一节的「X：可加于基字 …」清单。"""
    text = RULE_DOC.read_text(encoding="utf-8")
    body = text.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    lists: dict[str, set[str]] = {}
    for line in body.splitlines():
        match = re.match(r"-\s*\*\*(.)\*\*：.*?可加于基字(.*?)(?:（|$)", line)
        if not match:
            continue
        # 只留藏文字母，把顿号、空格和行尾的说明文字滤掉
        letters = {c for c in match.group(2) if "ཀ" <= c <= "ཬ"}
        lists[match.group(1)] = letters
    return lists


@pytest.mark.skipif(not RULE_DOC.exists(), reason="藏文规则.md 只在本地保留，不进仓库")
def test_rule_tables_match_the_spec_document():
    """代码里的规则表必须和「藏文规则.md」逐项一致。

    文档是规格，改文档不改代码这条就会红——避免两边各说各话。
    """
    assert set(_doc_lists("上加字")) == set(SUPERSCRIPTS)
    assert set(_doc_lists("下加字")) == set(SUBJOINED)
    assert set(_doc_lists("前加字")) == set(PREFIXES)

    for letter, roots in _doc_lists("上加字").items():
        assert roots == set(SUPER_ROOT_RULES[letter]), f"上加字 {letter}"
    for letter, roots in _doc_lists("下加字").items():
        assert roots == set(SUBJOINED_ROOT_RULES[letter]), f"下加字 {letter}"
    for letter, roots in _doc_lists("前加字").items():
        assert roots == set(PREFIX_ROOT_RULES[letter]), f"前加字 {letter}"

    # 文档里的 ཝ 特例，要和代码里的四个字串对上
    assert WAZUR_SPECIAL_STACKS == frozenset({"རྩ", "གྲ", "དྲ", "ཕྱ"})

    # 再后加字：文档说只认 ས，它可加于 ག ང བ མ
    assert SUFFIX2S == ("ས",)
    assert set(SUFFIX2_SUFFIX_RULES["ས"]) == {"ག", "ང", "བ", "མ"}
