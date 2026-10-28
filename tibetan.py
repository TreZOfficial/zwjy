# -*- coding: utf-8 -*-
"""
tibetan.py —— 藏文音节合成器与合法性校验
=========================================

本模块负责把「按组件录入」的结果，按藏文的 Unicode 组合规则，合成为正确的
藏文字符串。**它不是简单的字符串拼接**，原因见下。

组件语义顺序（也就是界面上从左到右的顺序）::

    前加字 + 上加字 + 基字 + 下加字 + 元音 + 后加字 + 再后加字

但真正写入 Unicode 字符时，只有「上加字」会造成顺序/形式变化：

* 前加字：正常辅音形式，直接输出。例如 ``བ``。
* 上加字：如果**有**上加字，则输出 ``上加字正常形式 + 基字的下加形式``；
  如果**没有**上加字，则输出 ``基字正常形式``。
  例如 ``ས`` + ``ཀ`` -> ``སྐ``（``ས`` 用正常形式，``ཀ`` 换成下加形式的 ``ྐ``）。
* 下加字：录入时用正常辅音形式选择，输出时换算成下加形式。
  例如 ``ཡ`` -> ``ྱ``，``ར`` -> ``ྲ``。
  **下加字可以叠多层**（是一个有序列表，上限见 :data:`MAX_SUBJOINED`）：
  藏文本土词汇里只有 ``གྲྭ``（ག+ྲ+ྭ）和 ``ཕྱྭ``（ཕ+ྱ+ྭ）这两个双下加字的词形，
  但梵文转写会出现更深的堆叠。按列表顺序依次追加即可，Unicode 侧无需特殊处理。
* 元音：直接追加在「基字（或其下加形式）之后、下加字之前」——也就是紧跟整个
  辅音堆叠之后。元音留空表示默认元音 a，不输出任何字符。
* 后加字、再后加字：正常辅音形式，直接输出。

一个完整的例子（要求文档中的用例）::

    前加字 བ + 上加字 ས + 基字 ག + 下加字 ར + 元音 ུ + 后加字 བ + 再后加字 ས
    = བ + ས + ྒ + ྲ + ུ + བ + ས
    = བསྒྲུབས

关于 Unicode 稳定性
-------------------
藏文区块中，「下加形式」全是 ``Canonical_Combining_Class = 0`` 的字符
（即它们不参与规范重排序），而元音符号 ``ི/ུ/ེ/ོ`` 的组合类互不相同。
因此按上面规则拼接出来的字符串天然满足 Unicode 规范顺序，
不需要额外的 ``unicodedata.normalize()``。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 一、内置藏文组件表
# ---------------------------------------------------------------------------
# 说明：这里的取值都是「正常辅音形式」。界面上直接展示这些字符供选择；
# 需要下加形式的字段（基字在上有上加字时、以及下加字字段本身）由下面的
# 映射表换算。

#: 前加字（སྔོན་འཇུག）共 5 个
PREFIXES: tuple[str, ...] = ("ག", "ད", "བ", "མ", "འ")

#: 上加字（མགོ་ཅན）共 3 个
SUPERSCRIPTS: tuple[str, ...] = ("ར", "ལ", "ས")

#: 基字（མིང་གཞི）共 30 个
ROOTS: tuple[str, ...] = (
    "ཀ", "ཁ", "ག", "ང", "ཅ", "ཆ", "ཇ", "ཉ", "ཏ", "ཐ",
    "ད", "ན", "པ", "ཕ", "བ", "མ", "ཙ", "ཚ", "ཛ", "ཝ",
    "ཞ", "ཟ", "འ", "ཡ", "ར", "ལ", "ཤ", "ས", "ཧ", "ཨ",
)

#: 下加字（འདོགས་ཅན）共 4 个，界面上按正常辅音形式选择
#:
#: 早先这里还有第 5 个 ཧ（下加形式 ྷ，只出现在 གྷ དྷ བྷ ཛྷ 这几个梵文转写里）。
#: 按「藏文规则.md」去掉了——那份规则只认 ཡ ར ལ ཝ，本工程现有的 330 条词库
#: 也确实一次都没用到 ྷ。真要录梵文转写再加回来。
SUBJOINED: tuple[str, ...] = ("ཡ", "ར", "ལ", "ཝ")

#: 元音（དབྱངས）共 4 个；留空表示默认元音 a
VOWELS: tuple[str, ...] = ("ི", "ུ", "ེ", "ོ")

#: 后加字（རྗེས་འཇུག）共 10 个
SUFFIXES: tuple[str, ...] = ("ག", "ང", "ད", "ན", "བ", "མ", "འ", "ར", "ལ", "ས")

#: 再后加字（ཡང་འཇུག）。规则文档只认 ས：
#: ད 虽然传统上算第二个再后加字，但现代藏文极少用，文档里明确「可认为只有一个」。
#: 现有词库 23 处再后加字全是 ས，没有 ད。
SUFFIX2S: tuple[str, ...] = ("ས",)

#: 各组件的中文名，供界面与报错信息复用
COMPONENT_LABELS: dict[str, str] = {
    "prefix": "前加字",
    "superscript": "上加字",
    "root": "基字",
    "subjoined": "下加字",
    "vowel": "元音",
    "suffix": "后加字",
    "suffix2": "再后加字",
}

# ---------------------------------------------------------------------------
# 二、正常形式 -> 下加形式 映射表
# ---------------------------------------------------------------------------
# 藏文 Unicode 区块里，下加字母区（U+0F90–U+0FBC）与正常辅音区
# （U+0F40–U+0F6C）之间保持着**固定的 0x50 偏移**：
#
#     ཀ U+0F40  ->  ྐ U+0F90
#     ག U+0F42  ->  ྒ U+0F92
#     ན U+0F53  ->  ྣ U+0FA3
#     ...
#
# 所以这里不手工抄写 30 组映射（抄写极易出错），而是按偏移量生成，
# 并由 tests/test_tibetan.py 校验若干关键映射与取值范围。
SUBJOINED_OFFSET: int = 0x50


def _to_subjoined(consonant: str) -> str:
    """把正常辅音形式换算成对应的下加形式。"""
    return chr(ord(consonant) + SUBJOINED_OFFSET)


#: 正常辅音 -> 下加形式（覆盖 30 个基字）
SUBJOINED_MAP: dict[str, str] = {c: _to_subjoined(c) for c in ROOTS}

#: 下加字下拉框的 5 个选项，同样由上面的偏移量换算得到
SUBJOINED_TAIL_MAP: dict[str, str] = {c: SUBJOINED_MAP[c] for c in SUBJOINED}

#: 藏文本土词汇里下加字最多叠两层（就是 གྲྭ 与 ཕྱྭ 这几种词形）。
#: 超过这个层数只可能出现在梵文转写文本里，校验时会给提示。
NATIVE_SUBJOINED_LIMIT: int = 2

#: 界面上允许叠加的下加字层数硬上限。比 :data:`NATIVE_SUBJOINED_LIMIT`
#: 多留一层余量，好让梵文转写里的三层堆叠也能录进来。
MAX_SUBJOINED: int = 3


def normalize_subjoined(value: Any) -> list[str]:
    """把「下加字」字段归一成字符串列表。

    兼容三种写法，这样旧备份、旧数据库里的单字符串组件也能照常读出来：

    * 新版（v2 起）：``["ར", "ཝ"]``
    * 旧版（v1）：``"ར"``
    * 空：``None`` / ``""`` / ``[]``
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return []

# 关于元音：**不需要映射表**。
#
# 下加字之所以要一张映射表，是因为字段里存的是「正常辅音形式」（ཡ），
# 输出时要换成另一个字符（ྱ）。元音不一样——字段里存的就是要输出的那个
# Unicode 元音符号本身（ི U+0F72 等），空串表示默认元音 a、不输出任何字符，
# 所以直接追加即可。可选值仍然是一张表，就是上面的 VOWELS。

#: 藏文间隔符（ཙེག），用于连接多个音节
TSHEG: str = "་"

#: 藏文句末的「ཤད」，U+0F0D TIBETAN MARK SHAD。
#:
#: 在连续行文里 ། 表示一个短语/句子的结束。单独的词语本来不需要它，
#: 但在**词汇表、字帖、生词卡**这类材料里，词头后面通常也带一个 ། 作为分隔
#: （ཀ། ཁ། ག། 这样一路排下去）。所以本工具把它做成每个词条自带的开关，
#: 默认打开，可以单独关掉。
SHAD: str = "།"

#: 另一个长得一模一样的间隔符：tsheg bstar（U+0F0C）。
#: 它只用于特定排版场合（如行末不断行），Unicode 里的兼容分解就是 U+0F0B。
#: 从网页或旧文档里复制藏文时很容易带进来，跟工具输出的 U+0F0B 不相等，
#: 会导致「搜索明明有这个词却搜不到」，所以统一归一成 U+0F0B。
TSHEG_BSTAR: str = "༌"

#: 这两个字母结尾的词语，后面**不写** །。
#: 规则见「藏文规则.md」：ཀ / ག 之后不再加 ཤད。
SHAD_ABSENT_AFTER: frozenset[str] = frozenset("ཀག")

#: 这个字母结尾的词语，要先写 ་ 再写 །（ང་།）。
#: 规则见「藏文规则.md」：ང 之后需要先加 tsheg 再加 ཤད。
TSHEG_BEFORE_SHAD_AFTER: frozenset[str] = frozenset("ང")


#: 词尾的「（可选 tsheg）+ 一个或多个 །」。先剥再补，反复保存才不会越加越多。
#:
#: 前面的 ་ 也要一起吃掉：ང 结尾的写法是 ང་།，如果只剥 །，
#: 再保存一次就会变成 ང་་།。
_TRAILING_SHAD_RE = re.compile(r"(?:་)?།+$")

#: 词尾的元音符号（U+0F71–U+0F7D）。判断「词尾字母」时要跳过它们——
#: ཀི 的词尾字母是 ཀ，不是最后一个码点 ི。
_TRAILING_VOWEL_RE = re.compile(r"[ཱ-ཽ]+$")


def final_letter(text: str) -> str:
    """取词尾的**字母**，跳过元音符号、并把下加形式换算回正常形式。

    两层坑都要绕开，否则规则只能生效一半：

    * 结尾带元音：`གོ` 的词尾字母是 `ག`，不是最后一个码点 `ོ`；
    * 有上加字时基字写成了下加形式：`སྐུ` 剥掉元音后是 `ྐ`，
      要换算回 `ཀ` 才能和规则表对上。

    只看最后一个字符，这两类词会被整个漏掉。
    """
    stripped = _TRAILING_VOWEL_RE.sub("", text or "")
    if not stripped:
        return ""
    last = stripped[-1]
    code = ord(last)
    if 0x0F90 <= code <= 0x0FBC:        # 下加形式 -> 正常形式
        return chr(code - SUBJOINED_OFFSET)
    return last


def strip_trailing_shad(text: str) -> str:
    """去掉词尾已有的 །（连同它前面那个 ་）。"""
    return _TRAILING_SHAD_RE.sub("", text)


def has_stack(text: str) -> bool:
    """最后一个音节里有没有上加字或下加字。

    有的话基字一定写成了下加形式（U+0F90–U+0FBC），从文本就能看出来：
    `སྐུ`（上加字 ས）里的 `ྐ`、`ལྔ`（上加字 ལ）里的 `ྔ`、`དྲྭ` 里的 `ྲ ྭ` 都算。

    这类音节**不适用** ཀ/ག/ང 的收尾规则，一律加 །——
    `སྐུ།`、`ལྔ།`、`སྒོ།` 都是要带 ། 的。
    """
    tail = (text or "").rsplit(TSHEG, 1)[-1]
    return any(0x0F90 <= ord(ch) <= 0x0FBC for ch in tail)


def append_trailing_shad(text: str) -> str:
    """按**词尾字母**决定怎么收尾。

    - 词尾是 ཀ / ག：不加任何标点（规则如此，不是漏了）
    - 词尾是 ང：先补一个 ་，再补 །
    - 其余：直接补 །

    「词尾字母」要跳过结尾的元音符号：`གོ` 算 `ག`、`ངོ` 算 `ང`。
    只按最后一个字符判断的话，带元音的词会全部漏掉。

    但**带上加字/下加字的不适用这套规则**——`སྐུ`、`ལྔ`、`སྒོ` 一律带 །
    （见 :func:`has_stack`）。所以规则只作用于「裸基字（可带元音）」这种结尾。

    传进来的文本应当已经去掉过尾部的 ། 和 ་།，否则连着调用会越加越多。
    """
    if not text:
        return text
    if has_stack(text):
        return text + SHAD
    last = final_letter(text)
    if last in SHAD_ABSENT_AFTER:
        return text
    if last in TSHEG_BEFORE_SHAD_AFTER:
        return text + TSHEG + SHAD
    return text + SHAD


def normalize_text(text: str) -> str:
    """把用户粘贴进来的藏文归一成工具使用的规范写法。

    做两件事：

    1. 把 tsheg bstar（U+0F0C）换成标准的 tsheg（U+0F0B）——
       两者渲染结果完全一样，但码点不同，不归一就没法互相匹配；
    2. 走一遍 NFC，把可能出现的组合字符整理成规范顺序。
    """
    if not text:
        return ""
    return unicodedata.normalize("NFC", text.replace(TSHEG_BSTAR, TSHEG))


# ---------------------------------------------------------------------------
# 三、音节数据结构
# ---------------------------------------------------------------------------
@dataclass
class Syllable:
    """一个藏文音节的全部组件。

    所有字段都是「正常辅音形式」或元音符号本身，空串表示该组件为空。
    """

    prefix: str = ""       # 前加字
    superscript: str = ""  # 上加字
    root: str = ""         # 基字（必填）
    # 下加字（正常形式，如 ཡ / ར / ལ / ཝ / ཧ）。可以叠多层，见 MAX_SUBJOINED。
    subjoined: list[str] = field(default_factory=list)
    vowel: str = ""        # 元音（ི/ུ/ེ/ོ），空串 = 默认元音 a
    suffix: str = ""       # 后加字
    suffix2: str = ""      # 再后加字

    def __post_init__(self) -> None:
        # 允许写法上偷懒：Syllable(subjoined="ར") 与 Syllable(subjoined=["ར"]) 等价
        self.subjoined = normalize_subjoined(self.subjoined)

    # -- 构造 / 序列化 -----------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Syllable":
        """从字典安全地构造音节。

        对残缺数据要尽量宽容，因为这里处理的是外部输入（导入的备份、手工改过的
        JSON），不能因为一条脏数据就让整个页面崩掉：

        * 传入的不是字典（例如 ``"syllables": "x"``）-> 当成空音节；
        * 字段值不是字符串（例如 ``{"root": 123}``）-> 转成字符串，
          这样后续校验会把它报成「不在内置组件表中」，而不是抛 TypeError；
        * ``None`` -> 空串；多余的键忽略。
        """
        if not isinstance(data, dict):
            return cls()

        fields: dict[str, Any] = {}
        for name in COMPONENT_LABELS:
            value = data.get(name)
            if value is None or value == "":
                fields[name] = ""
            elif isinstance(value, str):
                fields[name] = value
            else:
                fields[name] = str(value)

        # 下加字是列表，且要兼容 v1 备份里的单字符串写法
        fields["subjoined"] = normalize_subjoined(data.get("subjoined"))
        return cls(**fields)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    # -- 便捷查询 ----------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        """整张卡片都没填任何组件。"""
        return not any(asdict(self).values())

    @property
    def is_complete(self) -> bool:
        """是否具备最低限度的可合成条件（有基字）。"""
        return bool(self.root)

    def component(self, name: str) -> str:
        return getattr(self, name)


@dataclass
class WordComponents:
    """一个词条的完整组件结构：若干音节 + 两个排版开关。

    :param auto_join: 音节之间是否用 ་ 连接
    :param trailing_shad: 词尾是否加 །（ཤད），词汇表里通常要加
    """

    syllables: list[Syllable] = field(default_factory=list)
    auto_join: bool = True
    trailing_shad: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WordComponents":
        """从字典构造。**对畸形输入一律宽容，不抛异常。**

        这里处理的是外部输入（前端提交的 JSON、导入的备份文件），一条脏数据
        不该让整个请求 500。结构不对就当成没有音节，交给 :func:`validate_word`
        去报「请至少填写一个音节」，那是用户能看懂的信息。
        """
        if not isinstance(data, dict):
            return cls()
        raw_syllables = data.get("syllables")
        if not isinstance(raw_syllables, list):
            raw_syllables = []
        return cls(
            syllables=[Syllable.from_dict(item) for item in raw_syllables],
            auto_join=bool(data.get("auto_join", True)),
            trailing_shad=bool(data.get("trailing_shad", True)),
        )

    @classmethod
    def from_json(cls, raw: str | None) -> "WordComponents":
        """从数据库里存的 JSON 文本还原；解析失败时返回空结构而不是抛异常。"""
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            # v1：subjoined 是单个字符串
            # v2：subjoined 是字符串列表（支持多层下加字），v1 数据仍可正常读取
            "version": 2,
            "auto_join": self.auto_join,
            "trailing_shad": self.trailing_shad,
            "syllables": [s.to_dict() for s in self.syllables],
        }

    def to_json(self) -> str:
        """序列化成 JSON 文本（ensure_ascii=False，藏文原样保存便于阅读）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def meaningful_syllables(self) -> list[Syllable]:
        """过滤掉完全空白的音节卡片（用户点了「添加音节」但没填）。"""
        return [s for s in self.syllables if not s.is_empty]


# ---------------------------------------------------------------------------
# 四、合成
# ---------------------------------------------------------------------------
def compose_syllable(syllable: Syllable) -> str:
    """把单个音节合成成藏文字符串。

    没有基字时无法合成，返回空串（由 :func:`validate_syllable` 负责给出错误提示）。
    """
    if not syllable.is_complete:
        return ""

    parts: list[str] = []

    # 1) 前加字：正常形式
    if syllable.prefix:
        parts.append(syllable.prefix)

    # 2) 上加字 + 基字：这一步是合成规则的核心
    if syllable.superscript:
        # 有上加字 -> 上加字用正常形式，基字降为下加形式
        parts.append(syllable.superscript)
        parts.append(SUBJOINED_MAP.get(syllable.root, syllable.root))
    else:
        # 没有上加字 -> 基字保持正常形式
        parts.append(syllable.root)

    # 3) 下加字：按录入顺序逐层追加，每一层都换算成下加形式。
    #    Unicode 里下加字母的 Canonical_Combining_Class 全是 0（不参与规范重排序），
    #    所以列表顺序就是正确的编码顺序，比如 ག + ྲ + ྭ = གྲྭ。
    for letter in syllable.subjoined:
        parts.append(SUBJOINED_TAIL_MAP.get(letter, letter))

    # 4) 元音：追加在辅音堆叠之后；空串表示默认元音 a，不输出字符
    #    （字段里存的就是要输出的元音符号本身，不需要像下加字那样换算）
    if syllable.vowel:
        parts.append(syllable.vowel)

    # 5) 后加字、再后加字：正常形式
    if syllable.suffix:
        parts.append(syllable.suffix)
    if syllable.suffix2:
        parts.append(syllable.suffix2)

    return "".join(parts)


def compose_word(
    syllables: Iterable[Syllable] | WordComponents,
    auto_join: bool = True,
    trailing_shad: bool = False,
) -> str:
    """把多个音节合成一个藏文词语。

    :param syllables: 音节序列，或直接传 :class:`WordComponents`
    :param auto_join: True 时音节之间用藏文间隔符 ``་`` 连接；
                      False 时直接首尾相连，不加任何字符
    :param trailing_shad: 词尾是否追加 ``།``（ཤད）。
                          传 :class:`WordComponents` 时以它自己的字段为准。
    """
    if isinstance(syllables, WordComponents):
        auto_join = syllables.auto_join
        trailing_shad = syllables.trailing_shad
        syllables = syllables.syllables

    pieces = [compose_syllable(s) for s in syllables]
    pieces = [p for p in pieces if p]  # 丢弃无法合成的空音节
    word = (TSHEG if auto_join else "").join(pieces)
    # 一个音节都没合成出来时不加标点，避免出现只有标点、没有内容的空词。
    # 具体加什么由**词尾字母**决定：ཀ/ག 不加、ང 加 ་།、其余加 །（见 append_trailing_shad）
    if word and trailing_shad:
        word = append_trailing_shad(word)
    return word


# ---------------------------------------------------------------------------
# 五、传统藏文词典序
# ---------------------------------------------------------------------------
#: 三十个基字的先后（就是传统字母表 ཀ→ཨ 的顺序），也是所有组件的比较顺序。
_ROOT_RANK: dict[str, int] = {letter: index for index, letter in enumerate(ROOTS)}

#: 四个元音的先后
_VOWEL_RANK: dict[str, int] = {letter: index for index, letter in enumerate(VOWELS)}

#: 缺失的组件排在最前 —— 「没有」先于「有」。
_ABSENT_RANK: int = -1


def _letter_rank(letter: str) -> int:
    return _ROOT_RANK.get(letter, _ABSENT_RANK)


def dictionary_sort_key(syllables: Iterable[Syllable] | WordComponents) -> tuple:
    """生成传统藏文词典序的排序键。

    顺序：先比**基字**（ཀ→ཨ）；基字相同再依次比**前加字、上加字、
    下加字、后加字、再后加字、元音**，每一项同样按 ཀ→ཨ 的顺序。
    缺失的组件排在最前。

    注意这不是 Unicode 码点序——码点序会把 ཀ 后面所有「带前加字」的词
    拆散。真正的词典序要先看基字，忽略前加字与上加字。

    多音节词逐音节比较：先比第一个音节，完全相同的再比第二个。
    """
    if isinstance(syllables, Syllable):
        syllables = [syllables]
    elif isinstance(syllables, WordComponents):
        syllables = syllables.syllables

    keys = []
    for syllable in syllables:
        keys.append((
            _letter_rank(syllable.root),
            _letter_rank(syllable.prefix),
            _letter_rank(syllable.superscript),
            # 下加字可以叠多层，逐层比
            tuple(_letter_rank(letter) for letter in syllable.subjoined),
            _letter_rank(syllable.suffix),
            _letter_rank(syllable.suffix2),
            _VOWEL_RANK.get(syllable.vowel, _ABSENT_RANK),
        ))
    return tuple(keys)


# ---------------------------------------------------------------------------
# 六、合法性校验
# ---------------------------------------------------------------------------
# 下面这套规则属于「**常用搭配规则集**」，目的是在用户拼出明显不合藏文
# 正字法的组合时给出提醒。它不是完整的藏文正字法校验器：
#   * 所有提示都是「警告」，**不阻止保存**；
#   * 规则表只收录常见搭配，罕用但正确的组合可能被提示为「不常见」。
# 这一取舍是刻意的——宁可提示得保守一点，也不要误报。

#: 前加字 × 基字 的常见搭配
#:
#: 全部取自「藏文规则.md」的「前加字」一节，逐条对应：
#: ག 十一个、ད 六个、བ 十六个、མ 十一个、འ 十个。
PREFIX_ROOT_RULES: dict[str, frozenset[str]] = {
    # ག 可加于：ཅ ཏ ཙ ཉ ད ན ཞ ཟ ཡ ཤ ས
    "ག": frozenset("ཅཉཏདནཙཞཟཡཤས"),
    # ད 可加于：ཀ པ ག བ ང མ
    "ད": frozenset("ཀགངཔབམ"),
    # བ 可加于：ཀ ཅ ཏ ཙ ག ང ཇ ཉ ད ན ཛ ཞ ཟ ར ཤ ས
    #
    # 注意这里**含 ང ཇ ཛ、不含 ལ**，与早先的表正好相反。
    # 不含 ལ 是照文档执行，代价已知：བལ（羊毛）与 ཤིང་བལ（棉花）、
    # ལུག་བལ（羊毛）、བལ་པོ / བལ་ཡུལ（尼泊尔）这些词录入时会被提示
    # 「搭配不常见」——只是提示，不挡保存。真碰到了再加回来。
    "བ": frozenset("ཀགངཅཇཉཏདནཙཛཞཟརཤས"),
    # མ 可加于：ཁ ཆ ཐ ཚ ག ཇ ད ཛ ང ཉ ན（文档里没有 ཕ）
    "མ": frozenset("ཁགངཆཇཉཐདནཚཛ"),
    # འ 可加于：ག ཇ ད བ ཛ ཁ ཆ ཐ ཕ ཚ
    "འ": frozenset("ཁགཆཇཐདཕབཚཛ"),
}

#: 上加字 × 基字 的常见搭配
#:
#: 全部取自「藏文规则.md」的「上加字」一节：
#: ར 十二个、ལ 十个、ས 十一个。
#:
#: 早先这三张表是「超集」——为了让 སླ རླ 这类词不误报，额外收了 ལ、ས 等基字。
#: 现在按文档收紧：那几个组合本来就**有别的合法拆法**（见下面
#: _is_valid_subjoined_pair 的说明），走另一条路照样不报警告，
#: 不必再靠放宽上加上字表来兜。
SUPER_ROOT_RULES: dict[str, frozenset[str]] = {
    # ར 可加于：ཀ ཏ ཙ ག ཇ ད བ ཛ ང ཉ ན མ
    "ར": frozenset("ཀགངཇཉཏདནབམཙཛ"),
    # ལ 可加于：ཀ ཅ ཏ པ ག ཇ ད བ ང ཧ
    "ལ": frozenset("ཀགངཅཇཏདཔབཧ"),
    # ས 可加于：ཀ ཏ པ ཙ ག ད བ ང ཉ ན མ
    "ས": frozenset("ཀགངཉཏདནཔབམཙ"),
}

#: 下加字 × 基字 的常见搭配（键是下加字的正常形式）。
#: 全部取自「藏文规则.md」的「下加字」一节：
#: ཡ 七个、ར 十四个、ལ 六个、ཝ 十二个。
SUBJOINED_ROOT_RULES: dict[str, frozenset[str]] = {
    # ཡ 可加于：ཀ ཁ ག པ ཕ བ མ
    "ཡ": frozenset("ཀཁགཔཕབམ"),
    # ར 可加于：ཀ ཁ ག ཏ ཐ ད ན པ ཕ བ མ ཤ ས ཧ
    # （文档这里比传统教材多一个 ན）
    "ར": frozenset("ཀཁགཏཐདནཔཕབམཤསཧ"),
    # ལ 可加于：ཀ ག བ ར ས ཟ
    "ལ": frozenset("ཀགབརསཟ"),
    # ཝ 可加于：ཀ ཁ ག ཉ ད ཚ ཞ ཟ ར ལ ཤ ཧ
    # 另有四个特例不在这张表里，见下面的 WAZUR_SPECIAL_STACKS。
    "ཝ": frozenset("ཀཁགཉདཚཞཟརལཤཧ"),
}

#: 「藏文规则.md」里 ཝ 那条括号中的特例：རྩ、གྲ、དྲ、ཕྱ 这四个字下面也能加 ཝ。
#:
#: 注意它描述的是**合成之后的整串字**，不是单个基字，所以没法并进上面那张
#: 「下加字 × 基字」表里：
#:
#:     རྩ  ->  上位是上加字 ར、基字是 ཙ，单看基字 ཙ 并不在 ཝ 的表里
#:     其余三个  ->  本身就是「基字 + 第一层下加字」两层堆叠
#:
#: 校验时按当前音节实际拼出的字串来查这个集合。
WAZUR_SPECIAL_STACKS: frozenset[str] = frozenset({"རྩ", "གྲ", "དྲ", "ཕྱ"})

# ---------------------------------------------------------------------------
# 多层下加字的结构规则
# ---------------------------------------------------------------------------
# 藏文里一个音节最多只有一个「下加字本体」（ྱ ྲ ླ ྷ），此外还可以再跟一个
# ྭ（wa-zur）。所有已证实的双下加字词形都符合这个结构：
#
#     གྲྭ   基字 ག + ྲ + ྭ       དྲྭ   基字 ད + ྲ + ྭ   （དྲྭ་བ = 网）
#     ཕྱྭ   基字 ཕ + ྱ + ྭ
#
# 早期版本这里写死了一份「本土只有 གྲྭ 与 ཕྱྭ 两类」的二元白名单，
# 那是错的：དྲྭ་བ（网）就是个常用词，白名单会把这么常见的词误报成「极为罕见」。
# 与其继续猜一份永远可能不全的枚举，不如按上面这个**结构**来判：
# 第二层必须是 ྭ，第一层必须是有据可查的 ྲ 或 ྱ。
#
# 这里刻意**不锁基字**。代价是像 ཧྲྭ 这种并不存在的拼法也不会被提示；
# 但这个代价是划算的——见 tests/test_tibetan.py 里
# test_rule_does_not_pin_the_base_consonant 的说明。
# （注：早年有个版本曾把 ཧྲྭ 当成证据写进注释，那是从一份 OCR 错乱的
#   教材扫描件里读出来的，该拼法并不存在，已删除。）

#: 唯一能出现在第二层的下加字（wa-zur 的正常形式）
WAZUR_LETTER: str = "ཝ"

#: 有据可查的「下加字本体 + wa-zur」组合的第一层。
#: 即 ྲ་ཟུར(ྲ+ྭ) 与 ྱ་ཟུར(ྱ+ྭ) 这两种。
SUBJOINED_BEFORE_WAZUR: frozenset[str] = frozenset({"ཡ", "ར"})

#: 再后加字 × 后加字 的常见搭配（较罕见，仅作提示）
#:
#: 只有 ས 一项：规则文档把 ད 排除在再后加字之外（「现代藏文很少使用」），
#: :data:`SUFFIX2S` 里已经没有 ད，这里也就没有它的条目。
SUFFIX2_SUFFIX_RULES: dict[str, frozenset[str]] = {
    # ས 可加于后加字：ག ང བ མ
    "ས": frozenset("གངབམ"),
}

#: 不能搭配前加字 / 上加字 / 下加字的基字
ROOTS_WITHOUT_AFFIX: frozenset[str] = frozenset("འཨ")


@dataclass
class Issue:
    """一条校验结果。level 为 ``error``（无法合成）或 ``warning``（组合不常见）。"""

    level: str
    syllable_index: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "syllable_index": self.syllable_index,
            "message": self.message,
        }


def _syllable_desc(index: int, total: int) -> str:
    return f"第 {index + 1} 个音节" if total > 1 else "该音节"


def _is_valid_super_pair(superscript: str, root: str) -> bool:
    """上加字『superscript』配基字『root』是否属于常见搭配。"""
    return root in SUPER_ROOT_RULES.get(superscript, frozenset())


def _is_valid_prefix_pair(prefix: str, root: str) -> bool:
    """前加字『prefix』配基字『root』是否属于常见搭配。"""
    return root in PREFIX_ROOT_RULES.get(prefix, frozenset())


def _is_valid_subjoined_pair(root: str, subjoined: str) -> bool:
    """基字『root』配第一层下加字『subjoined』是否属于常见搭配。"""
    return root in SUBJOINED_ROOT_RULES.get(subjoined, frozenset())


def _super_ok(superscript: str, root: str) -> bool:
    """上加字『superscript』配基字『root』算不算过关。

    除了直接查表，还要看**另一种拆法**成不成立：同一串字常有不止一种拆法，
    两种拆法写出完全相同的字节——

        ས + ྲ    上加字 ས / 基字 ར    （སྲིད་པ 轮回、སྲོག 命）
        ས + ྲ    基字 ས / 下加字 ྲ

    用户只能挑一种拆法，但这串字算不算藏文跟挑哪种拆法无关，
    所以只要有一种读法成立就不该报警告。
    """
    return _is_valid_super_pair(superscript, root) or _is_valid_subjoined_pair(superscript, root)


def _first_subjoined_ok(superscript: str, root: str, first: str) -> bool:
    """基字『root』（可能带上加字『superscript』）下面加第一层『first』算不算过关。

    判定口径与 :func:`_super_ok` 一致：查表 + 「另一种拆法」。
    另外处理规则文档为 ཝ 开的特例（རྩྭ 的 ཝ 上面那段是 རྩ 而不是基字 ཙ）。
    """
    if not first:
        return True
    if _is_valid_subjoined_pair(root, first) or _is_valid_super_pair(root, first):
        return True
    if first == WAZUR_LETTER:
        stack = (superscript + SUBJOINED_MAP[root]) if superscript else root
        return stack in WAZUR_SPECIAL_STACKS
    return False


def validate_syllable(
    syllable: Syllable,
    index: int = 0,
    total: int = 1,
) -> list[Issue]:
    """校验单个音节，返回问题列表。

    ``error`` 级别表示无法合成（缺少基字或组件取值非法）；
    ``warning`` 级别表示组合在藏文正字法中不常见，但不阻止保存。
    """
    issues: list[Issue] = []
    where = _syllable_desc(index, total)

    # -- 硬性错误 ----------------------------------------------------------
    # 完全空白的音节卡片直接跳过：用户点了「添加音节」又没填是正常操作，
    # 合成时也会被忽略，不该当成错误拦下来。
    if syllable.is_empty:
        return issues

    if not syllable.root:
        issues.append(Issue("error", index, f"{where}：基字必选，请先选择基字。"))
        return issues

    # 组件取值是否来自内置组件表（下加字是多层列表，单独处理）
    checks = (
        ("prefix", "前加字", PREFIXES),
        ("superscript", "上加字", SUPERSCRIPTS),
        ("root", "基字", ROOTS),
        ("suffix", "后加字", SUFFIXES),
        ("suffix2", "再后加字", SUFFIX2S),
    )
    for name, label, allowed in checks:
        value = syllable.component(name)
        if value and value not in allowed:
            issues.append(
                Issue("error", index, f"{where}：{label}『{value}』不在内置组件表中。")
            )

    for position, letter in enumerate(syllable.subjoined):
        if letter not in SUBJOINED:
            label = "下加字" if position == 0 else f"第 {position + 1} 层下加字"
            issues.append(
                Issue("error", index, f"{where}：{label}『{letter}』不在内置组件表中。")
            )

    vowel = syllable.vowel
    if vowel and vowel not in VOWELS:
        issues.append(
            Issue("error", index, f"{where}：元音『{vowel}』不在内置组件表中。")
        )

    if any(i.level == "error" for i in issues):
        return issues

    # -- 软性警告 ----------------------------------------------------------
    root = syllable.root

    if syllable.prefix and root in ROOTS_WITHOUT_AFFIX:
        issues.append(
            Issue("warning", index,
                  f"{where}：基字『{root}』通常不加前加字，"
                  f"『{syllable.prefix}{root}』这一组合不常见。")
        )
    elif syllable.prefix and not _is_valid_prefix_pair(syllable.prefix, root):
        issues.append(
            Issue("warning", index,
                  f"{where}：前加字『{syllable.prefix}』与基字『{root}』"
                  f"的搭配不常见，请确认拼写。")
        )

    if syllable.superscript:
        if root in ROOTS_WITHOUT_AFFIX:
            issues.append(
                Issue("warning", index,
                      f"{where}：基字『{root}』通常不加在上加字下方。")
            )
        elif not _super_ok(syllable.superscript, root):
            issues.append(
                Issue("warning", index,
                      f"{where}：上加字『{syllable.superscript}』与基字『{root}』"
                      f"的搭配不常见，请确认拼写。")
            )
        # 注：上加字 + 基字 + 下加字 的三层堆叠（如 སྒྲ / རྒྱ / སྐྱ）是藏文
        # 里非常常见的结构，这里刻意不产生任何警告。

    subjoined = syllable.subjoined
    if subjoined:
        first = subjoined[0]

        # 第一层：与基字的搭配
        if root in ROOTS_WITHOUT_AFFIX:
            issues.append(
                Issue("warning", index, f"{where}：基字『{root}』通常不加下加字。")
            )
        elif not _first_subjoined_ok(syllable.superscript, root, first):
            issues.append(
                Issue("warning", index,
                      f"{where}：下加字『{first}』与基字『{root}』"
                      f"的搭配不常见，请确认拼写。")
            )

        # 同一个下加字重复叠加
        if len(set(subjoined)) != len(subjoined):
            issues.append(
                Issue("warning", index,
                      f"{where}：同一个下加字重复叠加了，请确认拼写。")
            )

        # 第二层及以后：按结构判，而不是按白名单判（见文件上方 WAZUR_LETTER 处的说明）
        if len(subjoined) == 2:
            body, second = subjoined
            if second != WAZUR_LETTER:
                issues.append(
                    Issue("warning", index,
                          f"{where}：第二层下加字『{second}』极为罕见——"
                          f"藏文里一个下加字之上几乎只会再跟一个 ྭ（wa-zur）。")
                )
            elif body not in SUBJOINED_BEFORE_WAZUR:
                issues.append(
                    Issue("warning", index,
                          f"{where}：下加字『{body}』之上再叠 ྭ（wa-zur）"
                          f"没有实际用例，请确认拼写。")
                )
        elif len(subjoined) > NATIVE_SUBJOINED_LIMIT:
            issues.append(
                Issue("warning", index,
                      f"{where}：叠加了 {len(subjoined)} 层下加字，"
                      f"这在藏文里只出现在梵文转写文本中。")
            )

        # 上加字 + 嵌套下加字 = 四层以上的堆叠，藏文里没有实际用例
        if syllable.superscript and len(subjoined) >= 2:
            issues.append(
                Issue("warning", index,
                      f"{where}：上加字、基字、两层下加字构成了四层堆叠，"
                      f"藏文中没有这样的用例，请确认拼写。")
            )

    if syllable.suffix2:
        suffix = syllable.suffix
        if not suffix:
            issues.append(
                Issue("error", index,
                      f"{where}：填写了再后加字，就必须先填写后加字。")
            )
        elif suffix not in SUFFIX2_SUFFIX_RULES.get(syllable.suffix2, frozenset()):
            issues.append(
                Issue("warning", index,
                      f"{where}：再后加字『{syllable.suffix2}』通常不跟在"
                      f"后加字『{suffix}』之后，请确认拼写。")
            )

    return issues


def validate_word(components: WordComponents) -> list[Issue]:
    """校验整个词条，返回全部问题（含空音节卡片的提示）。"""
    syllables = components.syllables
    total = len(syllables)
    issues: list[Issue] = []
    for index, syllable in enumerate(syllables):
        issues.extend(validate_syllable(syllable, index, total))

    # 全部为空 -> 直接提示整词未填写
    if not components.meaningful_syllables():
        return [Issue("error", 0, "请至少填写一个音节（基字为必选）。")]

    return issues


# ---------------------------------------------------------------------------
# 六、给界面用的辅助函数
# ---------------------------------------------------------------------------
#: 下拉框里表示「不选」的那一项显示什么。
#:
#: 用破折号而不是汉字「无」：下拉框为了把藏文显示清楚用的是 24px 的藏文字体，
#: 汉字字形套进藏文字体的行度量里，上缘会被裁掉；而原生 select 的弹出列表
#: 又会继承 select 的字号，没法「只在收起态把字缩小」。破折号在几乎任何字体里
#: 都是居中的一根横线，不挑字体度量，于是全局都能保持 24px。
#: 详细的三次踩坑记录见 static/style.css 里 .tibetan-select 那段注释。
NONE_LABEL: str = "—"


def build_component_options() -> dict[str, list[dict[str, str]]]:
    """生成 7 个下拉框的选项数据，供 Jinja2 模板渲染。

    每个下拉框的第一个选项都表示「不选」，显示为 :data:`NONE_LABEL`
    （基字除外，基字必选）。
    """
    def options(values: Iterable[str], *, allow_none: bool = True,
                none_label: str = NONE_LABEL) -> list[dict[str, str]]:
        items = ([{"value": "", "label": none_label}] if allow_none else [])
        items += [{"value": v, "label": v} for v in values]
        return items

    # 选项文案一律只占一个字符，别写「不加前加字」这种长句：
    # 下拉框的字号很大，长文案会撑破框子；是哪一项由它上方的字段名交代，写在这里是多余的。
    return {
        "prefix": options(PREFIXES),
        "superscript": options(SUPERSCRIPTS),
        "root": options(ROOTS, allow_none=False),
        "subjoined": options(SUBJOINED),
        "vowel": options(VOWELS),
        "suffix": options(SUFFIXES),
        "suffix2": options(SUFFIX2S),
    }


def allowed_components(syllable: Syllable | None = None) -> dict[str, list[str]]:
    """给定当前已选的组件，返回每个字段**还能选哪些值**。

    用途：录入页拿它把下拉框里不该选的选项置灰，用户就拼不出违反规则的组合，
    不用一边录一边看警告纠错。

    判定口径与 :func:`validate_syllable` 完全一致——都是调用上面那几个
    ``_xxx_ok`` 辅助函数，所以「列出来的」== 「不会报警告的」，
    两边不会各说各话。

    「无」选项（空字符串）不在返回的列表里：它永远可选，由前端保持不变。

    返回的 ``subjoined`` 是第一层下加字可选项，``subjoined_tail`` 是第二层
    及以后的可选项——按结构规则，第二层只可能是 ་ཝ（wa-zur），
    第一层不是 ཡ / ར 时则没有合法选项。
    """
    s = syllable if syllable is not None else Syllable()
    root = s.root
    subjoined = s.subjoined
    first = subjoined[0] if subjoined else ""

    # 基字列表永远要过滤——恰恰是「已经选了上加字、还没选基字」的时候，
    # 最需要把不合法的基字挡掉。
    #
    # 这里**故意用严格的表查询**（`_is_valid_super_pair` 等），而不是校验用的
    # 宽松判定（`_super_ok`）。两者差别在于「同一串字换个拆法也成立」的那些组合：
    #   * 校验宽松 —— 已经存进库的数据换个拆法写出来的字是一样的，不该回头报错；
    #   * 过滤严格 —— 界面上只提供文档认可的那种拆法，把用户引到规范写法上。
    # 例：基字 ལ 不会出现在「上加字 ས」的候选里（文档的 ས 表没有 ལ），
    # 用户会自然地改选「基字 ས + 下加字 ལ」，写出来还是同一个 སླ。
    has_affix = bool(s.prefix or s.superscript or first)
    root_opts = [
        r for r in ROOTS
        if not (has_affix and r in ROOTS_WITHOUT_AFFIX)   # འ / ཨ 不加前后缀
        and (not s.prefix or _is_valid_prefix_pair(s.prefix, r))
        and (not s.superscript or _is_valid_super_pair(s.superscript, r))
        and (not first or _first_subjoined_ok(s.superscript, r, first))
    ]

    if root and root not in ROOTS_WITHOUT_AFFIX:
        prefix_opts = [p for p in PREFIXES if _is_valid_prefix_pair(p, root)]
        super_opts = [x for x in SUPERSCRIPTS if _is_valid_super_pair(x, root)]
        sub_opts = [x for x in SUBJOINED if _first_subjoined_ok(s.superscript, root, x)]
    elif root:
        # 基字是 འ / ཨ：不接受任何前缀、上加字、下加字
        prefix_opts, super_opts, sub_opts = [], [], []
    else:
        prefix_opts = list(PREFIXES)
        super_opts = list(SUPERSCRIPTS)
        sub_opts = list(SUBJOINED)

    # 第二层及以后：只有 wa-zur 是有据可查的
    if not first:
        tail_opts = list(SUBJOINED)
    elif first in SUBJOINED_BEFORE_WAZUR:
        tail_opts = [WAZUR_LETTER]
    else:
        tail_opts = []

    return {
        "prefix": prefix_opts,
        "superscript": super_opts,
        "root": root_opts,
        "subjoined": sub_opts,
        "subjoined_tail": tail_opts,
        "vowel": list(VOWELS),
        "suffix": list(SUFFIXES),
        # 再后加字只跟后加字有关，没有后加字时一个都不能加
        "suffix2": [
            x for x in SUFFIX2S
            if s.suffix and s.suffix in SUFFIX2_SUFFIX_RULES.get(x, frozenset())
        ],
    }


def preview(raw_components: dict[str, Any]) -> dict[str, Any]:
    """给前端实时预览用：合成藏文并返回校验结果。

    入参是前端提交的 ``{"syllables": [...], "auto_join": bool}`` 结构。

    ``allowed`` 是每个音节的「还能选什么」（见 :func:`allowed_components`），
    前端拿它过滤下拉框。挂在预览接口上是为了省一次往返——页面每次改动
    本来就要打这个接口。
    """
    components = WordComponents.from_dict(raw_components)
    return {
        "tibetan": compose_word(components),
        "syllable_strings": [compose_syllable(s) for s in components.syllables],
        "issues": [issue.to_dict() for issue in validate_word(components)],
        "allowed": [allowed_components(s) for s in components.syllables],
    }
