# -*- coding: utf-8 -*-
"""藏文音节合成器的单元测试。

前三个用例是任务要求里明确指定的，其余覆盖边界情况。
"""

from __future__ import annotations

import unicodedata

from tibetan import (
    MAX_SUBJOINED,
    PREFIXES,
    ROOTS,
    SUBJOINED,
    SUBJOINED_BEFORE_WAZUR,
    SUBJOINED_MAP,
    SUBJOINED_TAIL_MAP,
    SUFFIX2S,
    SUFFIXES,
    SUPERSCRIPTS,
    TSHEG,
    VOWELS,
    WAZUR_LETTER,
    Syllable,
    WordComponents,
    compose_syllable,
    compose_word,
    normalize_subjoined,
    preview,
    validate_syllable,
    validate_word,
)


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
    """下加字字段要换算成下加形式。"""
    expected = {"ཡ": "ྱ", "ར": "ྲ", "ལ": "ླ", "ཝ": "ྭ", "ཧ": "ྷ"}
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
    """词汇表里的词头习惯带 །，所以默认打开。"""
    assert WordComponents().trailing_shad is True
    assert compose_word(WordComponents(syllables=[Syllable(root="ཀ")])) == "ཀ།"


def test_trailing_shad_can_be_turned_off():
    components = WordComponents(syllables=[Syllable(root="ཀ")], trailing_shad=False)
    assert compose_word(components) == "ཀ"


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
            syllables=[Syllable(root="ཀ")], trailing_shad=flagged
        )
        restored = WordComponents.from_json(original.to_json())
        assert restored.trailing_shad is flagged
        assert compose_word(restored) == compose_word(original)


def test_trailing_shad_is_not_a_component():
    """། 是排版开关，不是音节组件——校验时不该因为它报任何错。"""
    word = compose_word(WordComponents(syllables=[Syllable(root="ཀ")]))
    assert word == "ཀ།"
    issues = validate_word(WordComponents(syllables=[Syllable(root="ཀ")]))
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
    """ླ / ྷ 之上再叠 wa-zur 没有实际用例。"""
    for letter in ("ལ", "ཧ"):
        issues = validate_syllable(Syllable(root="ག", subjoined=[letter, "ཝ"]))
        assert any("wa-zur" in i.message for i in issues), letter


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
    assert PREFIXES == ("ག", "ད", "བ", "མ", "འ")
    assert SUPERSCRIPTS == ("ར", "ལ", "ས")
    assert len(ROOTS) == 30
    assert SUBJOINED == ("ཡ", "ར", "ལ", "ཝ", "ཧ")
    assert VOWELS == ("ི", "ུ", "ེ", "ོ")
    assert SUFFIXES == ("ག", "ང", "ད", "ན", "བ", "མ", "འ", "ར", "ལ", "ས")
    assert SUFFIX2S == ("ས", "ད")


def test_all_component_options_include_none_except_root():
    from tibetan import build_component_options

    options = build_component_options()
    assert set(options) == {
        "prefix", "superscript", "root", "subjoined", "vowel", "suffix", "suffix2"
    }
    # 基字必选，下拉框里没有「无」
    assert all(item["value"] for item in options["root"])
    assert len(options["root"]) == 30
    # 其余 6 个都有「无」选项（连元音也是——留空就是默认元音 a，
    # 这个说明写在字段名上，不占选项的宽度）
    for key in ("prefix", "superscript", "subjoined", "vowel", "suffix", "suffix2"):
        assert options[key][0]["value"] == "", key
        assert options[key][0]["label"] == "无", key


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
