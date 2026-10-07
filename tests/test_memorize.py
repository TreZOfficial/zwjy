# -*- coding: utf-8 -*-
"""藏语记忆工具（memorize/）的测试。

分三块：

1. **JS 合成器与 Python 合成器逐条比对**——网页里那份是 ``tibetan.py`` 的
   镜像，两份分开写就一定会走偏，所以这里用 dukpy 把页面上真实的脚本跑起来，
   对全部词条逐个比输出。
2. **更新工具**——归一化（两种输入格式）、id 稳定性、坏数据拒绝、原子写入。
3. **单文件约束**——不引用任何外部资源，词库确实内嵌在文件里。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORIZE_DIR = BASE_DIR / "memorize"
sys.path.insert(0, str(MEMORIZE_DIR))
sys.path.insert(0, str(BASE_DIR / "tests"))

from tibetan import (  # noqa: E402
    PREFIXES,
    ROOTS,
    SUBJOINED,
    SUBJOINED_TAIL_MAP,
    SUFFIX2S,
    SUFFIXES,
    SUPERSCRIPTS,
    VOWELS,
    Syllable,
    WordComponents,
    compose_word,
)

import update_wordbank as U  # noqa: E402

dukpy = pytest.importorskip("dukpy", reason="跑 JS 需要 dukpy（pip install dukpy）")

import js_harness as H  # noqa: E402

WORD_BANK_JSON = BASE_DIR / "data" / "期中词汇库.json"


def blank(**kwargs) -> dict:
    """一个空音节，覆盖上要测的字段。"""
    syl = {"prefix": "", "superscript": "", "root": "", "subjoined": [],
           "vowel": "", "suffix": "", "suffix2": ""}
    syl.update(kwargs)
    return syl


@pytest.fixture(scope="module")
def js():
    """页面脚本跑起来的解释器（整个模块共用一个，启动一次就够了）。"""
    return H.load_app()


def meaning_alts(meaning: str, js=None) -> list[str]:
    """调页面里的释义拆分函数。"""
    interp = js or H.load_app()
    return list(H.call(interp, "meaningAlternatives", meaning))


# ---------------------------------------------------------------------------
# 一、JS 合成器 vs Python 合成器
# ---------------------------------------------------------------------------
def test_page_parsed_its_own_wordbank(js):
    """页面一启动就应当把内嵌词库读出来。"""
    assert js.evaljs("window.__zwjyMemory.BANK.length") == len(H.extract_bank())


def test_component_tables_match_python(js):
    """两边的组件表必须一模一样。"""
    pairs = [
        ("ROOTS", ROOTS),
        ("SUPERSCRIPTS", SUPERSCRIPTS),
        ("SUBJOINED", SUBJOINED),
        ("VOWELS", VOWELS),
        ("PREFIXES", PREFIXES),
        ("SUFFIXES", SUFFIXES),
        ("SUFFIX2S", SUFFIX2S),
    ]
    for name, expected in pairs:
        js_value = js.evaljs(f"window.__zwjyMemory.{name}.join('')")
        assert js_value == "".join(expected), name


def test_subjoined_maps_match_python(js):
    """下加字的两张映射表（基字降格、下加字字段换算）也要一致。"""
    js_map = json.loads(js.evaljs("JSON.stringify(window.__zwjyMemory.SUBJOINED_MAP)"))
    assert js_map == {c: chr(ord(c) + 0x50) for c in ROOTS}

    js_tail = json.loads(js.evaljs("JSON.stringify(window.__zwjyMemory.SUBJOINED_TAIL)"))
    assert js_tail == SUBJOINED_TAIL_MAP


@pytest.mark.parametrize("label,syllable,expected", [
    ("基字+元音+后加字", blank(root="ཀ", vowel="ི", suffix="ག"), "ཀིག"),
    ("上加字+基字+元音", blank(superscript="ས", root="ཀ", vowel="ུ"), "སྐུ"),
    ("七组件齐全", blank(prefix="བ", superscript="ས", root="ག", subjoined=["ར"],
                        vowel="ུ", suffix="བ", suffix2="ས"), "བསྒྲུབས"),
    ("双下加字", blank(root="ད", subjoined=["ར", "ཝ"]), "དྲྭ"),
    ("默认元音 a", blank(root="ས"), "ས"),
    ("下加字 ཡ", blank(root="བ", subjoined=["ཡ"]), "བྱ"),
])
def test_js_composer_matches_known_values(js, label, syllable, expected):
    got = H.call(js, "composeWord", ("__json__", [syllable]), True, True)
    assert got == expected + "།", label


def test_js_composer_matches_python_on_every_bank_word(js):
    """核心测试：词库里每一条，JS 与 Python 合成出来的必须一模一样。

    这是防止两份合成器悄悄分家的主要手段。
    """
    bank = H.extract_bank()
    assert bank, "内嵌词库是空的，先跑一次 update_wordbank.py"

    mismatches = []
    for entry in bank:
        syllables = [Syllable.from_dict(s) for s in entry["components"]]
        expected = compose_word(WordComponents(
            syllables=syllables,
            auto_join=entry["autoJoin"],
            trailing_shad=entry["trailingShad"],
        ))
        got = H.call(js, "composeWord", ("__json__", entry["components"]),
                     entry["autoJoin"], entry["trailingShad"])
        if got != expected:
            mismatches.append((entry["tibetan"], expected, got))
    assert mismatches == [], f"JS 与 Python 合成结果不一致：{mismatches[:5]}"


def test_every_bank_word_composes_to_its_own_tibetan(js):
    """词库里存的藏文，必须等于它自己组件合成出来的结果。"""
    bad = []
    for entry in H.extract_bank():
        got = H.call(js, "composeWord", ("__json__", entry["components"]),
                     entry["autoJoin"], entry["trailingShad"])
        if got != entry["tibetan"]:
            bad.append((entry["tibetan"], got))
    assert bad == [], f"组件与藏文对不上：{bad[:5]}"


def test_js_normalize_text(js):
    """间隔符归一：tsheg bstar（U+0F0C）要变成标准 tsheg（U+0F0B）。"""
    assert H.call(js, "normalizeText", "དྲྭ༌བ") == "དྲྭ་བ"
    assert H.call(js, "normalizeText", "  བོད།  ") == "བོད།"
    assert H.call(js, "normalizeText", None) == ""


def test_js_normalize_for_compare_drops_trailing_shad(js):
    """判分时忽略词尾的 །（它是标点，不算拼写）。"""
    assert H.call(js, "normalizeForCompare", "བོད།") == "བོད"
    assert H.call(js, "normalizeForCompare", "བོད") == "བོད"
    assert H.call(js, "normalizeForCompare", "ཉི་མ།") == "ཉི་མ"
    # 中间的 ། 不能动
    assert H.call(js, "normalizeForCompare", "བོད།ཡིག") == "བོད།ཡིག"


def test_js_describe_syllable(js):
    got = H.call(js, "describeSyllable",
                 ("__json__", blank(root="བ", vowel="ོ", suffix="ད")))
    assert got == "基字 བ + 元音 ོ + 后加字 ད"

    got = H.call(js, "describeSyllable",
                 ("__json__", blank(prefix="བ", root="ག", subjoined=["ར"])))
    assert "前加字 བ" in got and "基字 ག" in got and "下加字 ར" in got


def test_js_composer_returns_empty_without_root(js):
    """没有基字就合成不出来，返回空串而不是硬拼。"""
    assert H.call(js, "composeWord", ("__json__", [blank(vowel="ི")]), True, True) == ""
    assert H.call(js, "composeWord", ("__json__", []), True, True) == ""


# ---------------------------------------------------------------------------
# 基字键盘的排布（按藏语启蒙课本的「四个一组、七组半」）
# ---------------------------------------------------------------------------
#: 课本上那张表的顺序：四个一组，从上往下七组半（4×7 + 2 = 30）
TRADITIONAL_ROOT_GROUPS = [
    "ཀཁགང", "ཅཆཇཉ", "ཏཐདན", "པཕབམ",
    "ཙཚཛཝ", "ཞཟའཡ", "རལཤས", "ཧཨ",
]


def test_root_order_follows_the_teaching_chart(js):
    """基字顺序必须是课本那张表，不能随手改。

    这个顺序既是传统的字母序，也是「四个一组」分组的前提——
    顺序一乱，四列排出来就不是课本上的样子了。
    """
    roots = js.evaljs("window.__zwjyMemory.ROOTS.join('')")
    assert len(roots) == 30
    chunks = [roots[i:i + 4] for i in range(0, 30, 4)]
    assert chunks == TRADITIONAL_ROOT_GROUPS


def test_root_keyboard_is_pinned_to_four_columns():
    """基字键盘的列数要钉死在 4。

    用 auto-fill 的话，手机上会变成六七个一行，就不是课本上那张表了。
    """
    html = H.read_html()
    css = re.search(r"<style>(.*?)</style>", html, re.DOTALL).group(1)
    rule = re.search(r"\.kb-keys\.roots\s*\{([^}]*)\}", css)
    assert rule, "缺少 .kb-keys.roots 规则"
    assert re.search(r"repeat\(\s*4\s*,", rule.group(1)), rule.group(1)
    # 列宽有上限，宽屏上排不满一行，要居中而不是靠左
    assert re.search(r"justify-content:\s*center", rule.group(1)), rule.group(1)

    # JS 里要在切到基字这一组时挂上这个类
    script = H.extract_app_script(html)
    assert "classList.toggle('roots'" in script


def test_root_group_is_the_default_tab(js):
    """打开软键盘先看到基字——用得最多（这份词库里 337 个音节都有基字）。"""
    html = H.read_html()
    script = H.extract_app_script(html)
    assert re.search(r"var activeGroup = 'root'", script)


# ---------------------------------------------------------------------------
# 藏译汉：中文释义的判分
# ---------------------------------------------------------------------------
def test_meaning_alternatives_strips_part_of_speech():
    """括号里多是词性标注（动）（现、未），不是释义，不该当成可接受答案。"""
    assert meaning_alts(("病、痛（动）")) == ["病", "痛"]
    assert meaning_alts("哭（现、未）") == ["哭"]
    assert meaning_alts("西藏；藏地") == ["西藏", "藏地"]
    assert meaning_alts("菱形孔格花纹") == ["菱形孔格花纹"]
    assert meaning_alts("") == []


@pytest.mark.parametrize("typed,meaning,expected", [
    # 完全一致
    ("西藏", "西藏；藏地", True),
    ("藏地", "西藏；藏地", True),
    # 多写几个字也算对
    ("西藏藏地", "西藏；藏地", True),
    ("嘴巴", "嘴，口", True),
    ("生病了", "病、痛（动）", True),
    # 写个大概也算对（中文近义词没法穷举）
    ("肚子", "腹、肚、腹部", True),
    ("花纹", "菱形孔格花纹", True),
    ("夜空", "天空；夜", True),
    # 但也不能太宽松
    ("中国", "西藏；藏地", False),
    ("鼻子", "嘴，口", False),
    ("动", "病、痛（动）", False),      # 只写了词性标注
    ("花", "菱形孔格花纹", False),       # 只写一个字，太零碎
    ("", "嘴，口", False),
    ("   ", "嘴，口", False),
])
def test_meaning_matches(js, typed, meaning, expected):
    """藏译汉的判分规则：宽严的分界线在这里钉死。"""
    assert bool(H.call(js, "meaningMatches", typed, meaning)) is expected


def test_meaning_matches_against_every_bank_meaning(js):
    """用词库里真实的释义自测：原文照抄一定要判对。"""
    for entry in H.extract_bank():
        assert H.call(js, "meaningMatches", entry["meaning"], entry["meaning"]), \
            entry["meaning"]


def test_zh2bo_and_bo2zh_both_have_an_answer_path(js):
    """两种模式都必须有「答对」的路径。

    回归测试：早先藏译汉只有「显示答案」一个按钮，点了直接记答错，
    于是没有任何办法答对 —— 队列永远清不空，一轮结束不了。
    """
    script = H.extract_app_script()
    # 藏译汉有输入框
    assert "meaning-input" in script
    assert "meaningMatches" in script
    # 提交按钮两种模式都走判分，而不是一种直接记错
    assert "gradeZh2Bo()" in script and "gradeBo2Zh()" in script
    # 旧的「点显示答案就记错」那段应该已经没有了
    assert "function selfGrade" not in script


def test_storage_key_is_namespaced_by_bank(js):
    """进度键要带上词库名。

    托管到 GitHub Pages 后，同一账号下的所有页面属于同一个源，
    localStorage 不区分路径 —— 键名不带词库名，两套词库的进度会互相覆盖。
    """
    script = H.extract_app_script()
    assert "data-bank-id" in H.read_html()
    assert "STORE_KEY" in script
    assert "'zwjy-memory:v1:'" in script or '"zwjy-memory:v1:"' in script
    # 键名是拼出来的，不是写死的常量
    assert "STORE_KEY = 'zwjy-memory:v1:' +" in script or \
           'STORE_KEY = "zwjy-memory:v1:" +' in script


def test_pages_mirror_is_up_to_date():
    """docs/index.html 是 GitHub Pages 的发布副本，必须和源文件一模一样。

    两份 HTML 各自演化是必然会发生的，而且很难发现——本地改了、线上还是旧版。
    所以更新工具一次写两处（memorize/index.html 是源，docs/ 是发布副本），
    这条测试保证没人绕过它手动改其中一份。
    """
    source = (BASE_DIR / "memorize" / "index.html").read_bytes()
    mirror = (BASE_DIR / "docs" / "index.html").read_bytes()
    assert source == mirror, (
        "两份不一致。改完 memorize/index.html 后要跑一次 "
        "`python memorize/update_wordbank.py 词库.json` 重新生成发布副本。"
    )


def test_pages_dir_has_nojekyll():
    """GitHub Pages 默认走 Jekyll，加个 .nojekyll 关掉，省事也快。"""
    assert (BASE_DIR / "docs" / ".nojekyll").exists()


def test_update_writes_the_mirror_it_is_given(tmp_path):
    """给 mirror 路径就同步一份，内容与源文件一致。"""
    html = tmp_path / "index.html"
    html.write_text(empty_bank_html(), encoding="utf-8")
    mirror = tmp_path / "published" / "index.html"

    report = U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00",
                      mirror=mirror)

    assert report["mirrored"] == [str(mirror)]
    assert mirror.read_text(encoding="utf-8") == html.read_text(encoding="utf-8")


def test_update_does_not_touch_anything_without_a_mirror(tmp_path):
    """不传 mirror 就只写源文件 —— 绝不能有「不传参数也改别的文件」的副作用。

    回归测试：早先 mirror 默认写死成模块级的 docs/index.html，结果测试用临时
    词库跑一遍更新，就把真实的发布文件覆盖成了 1 条假数据。
    """
    html = tmp_path / "index.html"
    html.write_text(empty_bank_html(), encoding="utf-8")

    before = U.MIRROR_HTML.read_bytes()
    report = U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")
    assert report["mirrored"] == []
    assert U.MIRROR_HTML.read_bytes() == before, "不该碰发布副本"


def test_updater_writes_bank_id(tmp_path):
    html = tmp_path / "index.html"
    html.write_text(empty_bank_html(), encoding="utf-8")
    U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")

    text = html.read_text(encoding="utf-8")
    assert 'data-bank-id="期中词汇库"' in text

    # 换一个 --bank-id 也能生效
    U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00", bank_id="期末")
    assert 'data-bank-id="期末"' in html.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 二、更新工具
# ---------------------------------------------------------------------------
def test_load_real_wordbank():
    entries = U.load_wordbank(WORD_BANK_JSON)
    assert len(entries) == 196
    for entry in entries:
        assert entry["tibetan"] and entry["meaning"]
        assert entry["id"] and entry["components"]


def test_ids_are_stable_and_meaning_edits_do_not_reset_progress():
    """id 必须只跟藏文走：改释义不该换 id，否则订正一个字进度就没了。"""
    first = U.load_wordbank(WORD_BANK_JSON)
    second = U.load_wordbank(WORD_BANK_JSON)
    assert [e["id"] for e in first] == [e["id"] for e in second]

    edited = json.loads(WORD_BANK_JSON.read_text(encoding="utf-8"))
    edited["words"][0]["meaning"] = "改过的释义"
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_edited.json")
    tmp.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    try:
        after = U.load_wordbank(tmp)
    finally:
        tmp.unlink()
    assert after[0]["id"] == first[0]["id"]
    assert after[0]["meaning"] == "改过的释义"


def test_adding_a_word_does_not_shift_other_ids():
    """插一条新词不能让别人 id 全变——用数组下标当 id 就会出这种事。"""
    original = json.loads(WORD_BANK_JSON.read_text(encoding="utf-8"))
    before = U.load_wordbank(WORD_BANK_JSON)

    original["words"].insert(0, {"tibetan": "ཨོཾ།", "meaning": "插入的新词",
                                 "components": blank(root="ཨ")})
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_inserted.json")
    tmp.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    try:
        after = {e["id"] for e in U.load_wordbank(tmp)}
    finally:
        tmp.unlink()

    for entry in before:
        assert entry["id"] in after, entry["tibetan"]


def test_flat_format_from_requirement_doc():
    """要求.md 里那种扁平写法也要能读：中文键名，下加字写的是下加形式。"""
    # 注意：要求.md 里那个例子本身是**不自洽**的——组件的元音 ི 与后加字 ས
    # 合成出来是 བཀྲིས，而它写的藏文是两音节的 བཀྲ་ཤིས་。这里给一组自洽的。
    raw = [{
        "id": "1",
        "tibetan": "བཀྲིས།",
        "meaning": "吉祥",
        "tags": ["常用"],
        "components": {"前加字": "བ", "上加字": "", "基字": "ཀ", "下加字": "ྲ",
                       "元音": "ི", "后加字": "ས", "再后加字": ""},
    }]
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_flat.json")
    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    try:
        entries = U.load_wordbank(tmp)
    finally:
        tmp.unlink()

    assert len(entries) == 1
    syllable = entries[0]["components"][0]
    # 关键：下加形式 ྲ 要换算回正常形式 ར，否则合成出来会多一个下加字符
    assert syllable["subjoined"] == ["ར"]
    assert syllable["root"] == "ཀ"

    # 单音节词：组件能合成出它的藏文
    fixed, _ = U.check_entries(entries)
    assert fixed[0]["tibetan"] == "བཀྲིས།"


def test_flat_format_only_holds_one_syllable():
    """要求.md 的扁平写法一个词只描述**一个音节**，多音节词必须拆成数组。

    它自己的例子 `བཀྲ་ཤིས་` 是两音节，却只给了 `བཀྲ` 的组件——照着写进去会
    被校验挡下来（合成结果覆盖不了整个词），这正是我们想要的行为：
    宁可不收，也不要收一份描述不全的组件。
    """
    lossy = [{
        "tibetan": "བཀྲ་ཤིས།",
        "meaning": "吉祥",
        "components": {"前加字": "བ", "基字": "ཀ", "下加字": "ྲ", "元音": "ི"},
    }]
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_lossy.json")
    tmp.write_text(json.dumps(lossy, ensure_ascii=False), encoding="utf-8")
    try:
        entries = U.load_wordbank(tmp)
    finally:
        tmp.unlink()

    with pytest.raises(U.WordbankError):
        U.check_entries(entries)

    # 拆成音节数组就没问题了
    complete = [{
        "tibetan": "བཀྲ་ཤིས།",
        "meaning": "吉祥",
        "components": [
            {"前加字": "བ", "基字": "ཀ", "下加字": "ྲ"},
            {"基字": "ཤ", "元音": "ི", "后加字": "ས"},
        ],
    }]
    tmp2 = Path(WORD_BANK_JSON.parent / "_tmp_complete.json")
    tmp2.write_text(json.dumps(complete, ensure_ascii=False), encoding="utf-8")
    try:
        entries = U.load_wordbank(tmp2)
        fixed, _ = U.check_entries(entries)
    finally:
        tmp2.unlink()
    assert fixed[0]["tibetan"] == "བཀྲ་ཤིས།"


def test_flat_format_rejects_bad_composition():
    """扁平格式同样要走合成校验。"""
    raw = [{"tibetan": "བོད།", "meaning": "对不上",
            "components": {"基字": "མ", "元音": "ི"}}]
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_bad.json")
    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    try:
        entries = U.load_wordbank(tmp)
        with pytest.raises(U.WordbankError) as excinfo:
            U.check_entries(entries)
        assert "对不上" in str(excinfo.value)
    finally:
        tmp.unlink()


def test_mismatch_can_be_fixed_by_recomposing():
    """--fix 时以组件为准改写藏文，改写结果要和用同一套参数合成的一致。"""
    raw = [{"tibetan": "བོད", "meaning": "x",
            "components": {"基字": "བ", "元音": "ོ", "后加字": "ད", "再后加字": ""}}]
    entries = [U._entry_from_any(raw[0], set())]
    # 这条 tibetan 结尾没有 །，推断出来的 trailingShad 就是 False
    assert entries[0]["trailingShad"] is False
    assert entries[0]["tibetan"] == "བོད"

    # 把 tibetan 改成对不上的值，再让 --fix 修回来
    entries[0]["tibetan"] = "错的"
    fixed, report = U.check_entries(entries, fix=True)
    assert report["mismatched"]
    assert fixed[0]["tibetan"] == compose_word(WordComponents(
        syllables=[Syllable.from_dict(s) for s in fixed[0]["components"]],
        auto_join=fixed[0]["autoJoin"],
        trailing_shad=fixed[0]["trailingShad"],
    ))
    assert fixed[0]["tibetan"] == "བོད"


def test_empty_wordbank_is_refused():
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_empty.json")
    tmp.write_text("[]", encoding="utf-8")
    try:
        with pytest.raises(U.WordbankError):
            U.load_wordbank(tmp)
    finally:
        tmp.unlink()


def test_wordbank_without_tibetan_is_refused():
    tmp = Path(WORD_BANK_JSON.parent / "_tmp_notb.json")
    tmp.write_text('[{"meaning": "没有藏文"}]', encoding="utf-8")
    try:
        with pytest.raises(U.WordbankError):
            U.load_wordbank(tmp)
    finally:
        tmp.unlink()


def empty_bank_html() -> str:
    """页面模板，但把内嵌词库清空——用来测「从零写入」这条路径。"""
    return re.sub(
        r'(<script type="application/json" id="wordbank"[^>]*>).*?(</script>)',
        r"\1\n[]\n\2",
        H.read_html(),
        flags=re.DOTALL,
    )


def test_update_is_idempotent(tmp_path):
    """同样的输入跑两次，文件内容必须完全一样（不然每次更新都是大 diff）。"""
    html = tmp_path / "index.html"
    html.write_text(H.read_html(), encoding="utf-8")

    U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")
    once = html.read_text(encoding="utf-8")
    U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")
    twice = html.read_text(encoding="utf-8")

    assert once == twice
    assert len(U.read_embedded(once)) == 196


def test_update_reports_changes(tmp_path):
    html = tmp_path / "index.html"
    html.write_text(empty_bank_html(), encoding="utf-8")

    first = U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")
    assert first["old_count"] == 0 and first["added"] == 196

    second = U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")
    assert second["added"] == 0 and second["removed"] == 0 and second["changed"] == 0

    # 删掉一条再看报告
    data = json.loads(WORD_BANK_JSON.read_text(encoding="utf-8"))
    data["words"] = data["words"][1:]
    smaller = tmp_path / "smaller.json"
    smaller.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    third = U.update(html, smaller, generated_at="2026-01-01 00:00")
    assert third["removed"] == 1 and third["added"] == 0
    assert len(U.read_embedded(html.read_text(encoding="utf-8"))) == 195


def test_update_leaves_html_alone_when_wordbank_is_bad(tmp_path):
    """词库有问题时绝不能动 HTML——半新半旧最糟糕。"""
    html = tmp_path / "index.html"
    html.write_text(H.read_html(), encoding="utf-8")
    U.update(html, WORD_BANK_JSON, generated_at="2026-01-01 00:00")
    before = html.read_text(encoding="utf-8")

    bad = tmp_path / "bad.json"
    bad.write_text('[{"tibetan": "བོད།", "meaning": "x", '
                   '"components": {"基字": "མ", "元音": "ི"}}]', encoding="utf-8")
    with pytest.raises(U.WordbankError):
        U.update(html, bad, generated_at="2026-01-01 00:00")
    assert html.read_text(encoding="utf-8") == before


def test_report_distinguishes_meaning_edit_from_tibetan_rewrite():
    """报告要分清楚两种「改动」——它们的后果完全不同。

    * 改释义/标签：id 不变，**进度保留**
    * 改藏文写法：id 变了，只能表现为「删一条 + 增一条」，**进度重置**

    早先的实现把两者混在一起，还统一标成「进度会重置」，
    对只改了释义的情况是错的；而真正会重置的那种又藏在新增/删除里看不出来。
    """
    old = [
        {"id": "aaa", "tibetan": "ཁ།", "meaning": "嘴，口", "tags": []},
        {"id": "bbb", "tibetan": "ང།", "meaning": "我", "tags": []},
    ]
    new = [
        # 同一个 id，只改了释义 -> 进度保留
        {"id": "aaa", "tibetan": "ཁ།", "meaning": "嘴、口（订正）", "tags": []},
        # 藏文改写 -> 新 id，表现为删+增
        {"id": "ccc", "tibetan": "ང་ཡི།", "meaning": "我", "tags": []},
    ]
    diff = U.diff_banks(old, new)

    assert diff["changed"] == ["aaa"]
    assert diff["renamed"] == [("ང།", "ང་ཡི།")]
    # 被认出来的「改写」不该再出现在新增/删除里，否则计数会让人困惑
    assert diff["pure_added"] == []
    assert diff["pure_removed"] == []
    assert diff["added"] == ["ccc"] and diff["removed"] == ["bbb"]


def test_report_keeps_unpaired_adds_and_removes():
    """释义对不上的增删就是纯粹的增删，不能硬配对。"""
    old = [{"id": "a", "tibetan": "ཁ།", "meaning": "嘴", "tags": []}]
    new = [{"id": "b", "tibetan": "ཆུ།", "meaning": "水", "tags": []}]
    diff = U.diff_banks(old, new)
    assert diff["renamed"] == []
    assert diff["pure_added"] == ["b"]
    assert diff["pure_removed"] == ["a"]


def test_update_refuses_html_without_the_wordbank_tag(tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html><body>没有词库标签</body></html>", encoding="utf-8")
    with pytest.raises(U.WordbankError):
        U.update(html, WORD_BANK_JSON)


def test_bank_body_escapes_closing_script_tag(tmp_path):
    """词条内容里万一出现 </script>，不能让它把标签提前闭合。"""
    html = tmp_path / "index.html"
    html.write_text(H.read_html(), encoding="utf-8")

    data = {"words": [{"tibetan": "བོད།", "meaning": "含 </script> 的释义",
                       "components": blank(root="བ", vowel="ོ", suffix="ད")}]}
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    U.update(html, weird, generated_at="2026-01-01 00:00")
    text = html.read_text(encoding="utf-8")
    assert "<\\/script>" in text
    # 解析回来仍然是对的
    assert U.read_embedded(text)[0]["meaning"] == "含 </script> 的释义"


def test_make_output_safe_does_not_crash():
    """GBK 控制台印藏文不能把脚本搞崩（Windows 默认就是 GBK）。"""
    assert U._make_output_safe() is not None or True    # 不抛异常即可
    assert U._word_list([]) == ""
    assert U._word_list(["བོད"]) != ""                  # 能显示就显示，不能就报数量


# ---------------------------------------------------------------------------
# 三、单文件约束
# ---------------------------------------------------------------------------
def test_html_is_a_single_self_contained_file():
    """纯离线：不引任何外部资源。"""
    html = H.read_html()
    assert "<link" not in html.lower(), "不该有外部样式表"
    for pattern in (r'src\s*=\s*["\']https?:', r'href\s*=\s*["\']https?:',
                    r'@import', r'fetch\s*\(', r'XMLHttpRequest'):
        assert not re.search(pattern, html, re.I), f"发现了外部引用：{pattern}"


def test_wordbank_is_embedded_in_the_html():
    html = H.read_html()
    assert "id=\"wordbank\"" in html
    assert len(H.extract_bank(html)) > 0
    # 词库不能同时以外部文件形式引用
    assert not re.search(r'wordbank[^>]*src=', html, re.I)


def test_html_declares_charset_and_viewport():
    html = H.read_html()
    assert 'charset="UTF-8"' in html
    assert "viewport" in html


def test_every_getelementbyid_target_exists():
    """JS 里取的每个 id 都得在 HTML 里真的存在。

    测试用的 DOM 替身对任何 id 都返回一个桩对象，所以拼错 id 在测试里不会报错，
    到了浏览器就是 null.addEventListener 直接白屏。这条静态检查专门堵这个洞。
    """
    html = H.read_html()
    script = H.extract_app_script(html)
    markup = H.strip_bank_block(html)

    declared = set(re.findall(r'\bid="([^"]+)"', markup))
    used = set(re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", script))
    # 脚本里拼出来的 id（比如 createElement 之后赋的）不算
    used = {u for u in used if " " not in u and "+" not in u}

    missing = sorted(used - declared)
    assert missing == [], f"JS 取了不存在的元素 id：{missing}"


def test_every_queried_class_exists():
    """querySelectorAll('.x') 里的类名应当在 HTML 或 CSS 里出现过。"""
    html = H.read_html()
    script = H.extract_app_script(html)
    css = re.search(r"<style>(.*?)</style>", html, re.DOTALL).group(1)
    known = set(re.findall(r'class="([^"]+)"', html))
    declared = {c for group in known for c in group.split()}
    declared |= set(re.findall(r"\.([a-zA-Z][\w-]*)", css))

    used = set(re.findall(r"querySelectorAll\(\s*['\"]\.([\w-]+)['\"]", script))
    used |= set(re.findall(r"querySelector\(\s*['\"]\.([\w-]+)['\"]", script))
    missing = sorted(used - declared)
    assert missing == [], f"JS 查了没有定义的类名：{missing}"
