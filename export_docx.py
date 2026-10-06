# -*- coding: utf-8 -*-
"""
export_docx.py —— 用 python-docx 导出 Word 背词表
=================================================

产出结构::

    文档标题（可选）
    ┌────────────┬────────────┐
    │ 藏文词语    │ 释义 / 空白 │
    ├────────────┼────────────┤
    │ ...        │ ...        │
    └────────────┴────────────┘
    页脚：第 X 页 / 共 Y 页

关于藏文在 Word 里的显示
------------------------
Word 把藏文（U+0F00–U+0FFF）当作**复杂文本**处理，取的是 run 属性
``w:rFonts/@w:cs`` 指定的字体，字号取 ``w:szCs``（而不是 ``w:sz``）。
如果只设 ``run.font.name``（python-docx 只会写 ascii/hAnsi），Word 在
Windows 上会退回到默认的复杂文本字体，藏文就会显示成方框或错位。
因此这里统一走 :func:`_style_run`，把 ascii / hAnsi / cs / eastAsia
四个字体属性、``w:sz`` 与 ``w:szCs`` 一起写全。
"""

from __future__ import annotations

import io
import random
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph

# ---------------------------------------------------------------------------
# 字体常量
# ---------------------------------------------------------------------------
#: 候选的藏文复杂文本字体，按优先级排列（导出设置里也可以手动改）
TIBETAN_FONT_CANDIDATES: tuple[str, ...] = (
    "Microsoft Himalaya",   # Windows 自带，通常无需额外安装
    "Noto Sans Tibetan",    # 开源，跨平台
    "Jomolhari",            # 开源，藏文风格贴近传统
    "Kailasa",              # Windows 自带的另一款藏文字体
)

DEFAULT_TIBETAN_FONT = TIBETAN_FONT_CANDIDATES[0]

#: 中文正文字体
CHINESE_FONT = "宋体"
#: 西文/数字字体
LATIN_FONT = "Times New Roman"

#: A4 页面可用宽度（21cm - 左右各 2.5cm 页边距）
CONTENT_WIDTH_CM = 16.0
TIBETAN_COLUMN_CM = 7.0

#: 顺序导出 / 乱序导出
ORDER_SEQUENTIAL = "sequential"
ORDER_RANDOM = "random"
ORDER_LABELS = {ORDER_SEQUENTIAL: "顺序", ORDER_RANDOM: "乱序"}

#: 排版方式。默认分栏——背词表按栏排比按表格排更像一份词汇表，
#: 而且一页能放下更多词条。
LAYOUT_COLUMNS = "columns"
LAYOUT_TABLE = "table"
LAYOUT_LABELS = {LAYOUT_COLUMNS: "分栏", LAYOUT_TABLE: "表格"}

#: 可选栏数。3 栏是默认值（横向 A4 时每栏约 8.4cm）。
COLUMN_CHOICES = (2, 3, 4)
DEFAULT_COLUMN_COUNT = 3

#: 分栏时每栏之间留的空隙（twips，1cm = 567twips，这里约 0.5cm）
COLUMN_SPACING_TWIPS = 283

#: 导出设置里可选的藏文字号（磅）。
#:
#: 默认给得比中文正文大不少：藏文字体为了把三四层高的辅音堆叠塞进一个字身，
#: 单个基字只占 em 的 40% 上下——同样写 12pt，中文是正常大小，藏文看着只有
#: 5pt，打印出来根本看不清。所以藏文单独用一个字号。
#: 导出设置里也可以调整，见 :data:`DEFAULT_TIBETAN_SIZE`。
TIBETAN_SIZE_CHOICES: tuple[float, ...] = (16.0, 20.0, 24.0, 28.0)
DEFAULT_TIBETAN_SIZE: float = 20.0
TIBETAN_SIZE_LABELS: dict[float, str] = {
    16.0: "小（16 磅）",
    20.0: "中（20 磅，默认）",
    24.0: "大（24 磅）",
    28.0: "特大（28 磅）",
}

#: 分栏排版时，藏文与释义之间的制表位位置（cm）上限。
#: 实际取值会按栏宽自适应（见 :func:`_column_tab_cm`）：
#: 栏窄的时候把制表位往前挪，给释义留出宽度。
COLUMN_TAB_CM = 3.0

#: 制表位最靠左的位置，再窄就不成样子了
MIN_COLUMN_TAB_CM = 2.0

#: 制表位大约占栏宽的比例
COLUMN_TAB_RATIO = 0.42

# ---------------------------------------------------------------------------
# OOXML 底层工具
# ---------------------------------------------------------------------------
# OOXML 对 rPr 子元素的顺序有严格要求（CT_RPr 是 sequence），
# 顺序写错的话 Word 会直接报「文件已损坏」。这里按规范列出顺序，
# 新元素总是插到第一个「应该排在我后面」的元素之前。
_RPR_ORDER: tuple[str, ...] = (
    "w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs", "w:caps",
    "w:smallCaps", "w:strike", "w:dstrike", "w:outline", "w:shadow",
    "w:emboss", "w:imprint", "w:noProof", "w:snapToGrid", "w:vanish",
    "w:webHidden", "w:color", "w:spacing", "w:w", "w:kern", "w:position",
    "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd",
    "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang",
    "w:eastAsianLayout", "w:specVanish", "w:oMath",
)


def _get_or_add_rpr_child(rPr, tag: str):
    """取出（或按 OOXML 规定顺序新建）rPr 下的某个子元素。"""
    element = rPr.find(qn(tag))
    if element is not None:
        return element

    element = OxmlElement(tag)
    if tag in _RPR_ORDER:
        position = _RPR_ORDER.index(tag)
        for child in rPr:
            child_tag = "w:" + child.tag.split("}")[-1]
            if child_tag in _RPR_ORDER and _RPR_ORDER.index(child_tag) > position:
                child.addprevious(element)
                break
        else:
            rPr.append(element)
    else:
        rPr.append(element)
    return element


def _style_run(
    run,
    *,
    size_pt: float | None = None,
    bold: bool = False,
    ascii_font: str = LATIN_FONT,
    east_asia_font: str = CHINESE_FONT,
    cs_font: str = LATIN_FONT,
):
    """给一个 run 写全字体与字号属性。

    关键在于：字号要同时写 ``w:sz``（西文/东亚）和 ``w:szCs``（复杂文本），
    字体要同时写 ``w:ascii`` / ``w:hAnsi`` / ``w:cs`` / ``w:eastAsia`` 四个属性。
    """
    rPr = run._element.get_or_add_rPr()

    rFonts = _get_or_add_rpr_child(rPr, "w:rFonts")
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)
    rFonts.set(qn("w:cs"), cs_font)
    rFonts.set(qn("w:eastAsia"), east_asia_font)

    if bold:
        _get_or_add_rpr_child(rPr, "w:b")
        _get_or_add_rpr_child(rPr, "w:bCs")

    if size_pt is not None:
        half_points = str(int(round(size_pt * 2)))
        _get_or_add_rpr_child(rPr, "w:sz").set(qn("w:val"), half_points)
        # w:szCs —— 没有它，Word 会用默认字号渲染藏文
        _get_or_add_rpr_child(rPr, "w:szCs").set(qn("w:val"), half_points)

    return run


def _add_field(paragraph: Paragraph, instruction: str) -> Any:
    """在段落里插入一个域（如 PAGE / NUMPAGES）。"""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")

    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)
    _style_run(run, size_pt=10.5)
    return run


# ---------------------------------------------------------------------------
# 导出参数
# ---------------------------------------------------------------------------
@dataclass
class ExportOptions:
    """一次导出的全部参数。"""

    title: str = ""
    order: str = ORDER_SEQUENTIAL
    include_meaning: bool = True
    hide_meaning: bool = False
    seed: str = ""
    tibetan_font: str = DEFAULT_TIBETAN_FONT
    tibetan_size_pt: float = DEFAULT_TIBETAN_SIZE
    meaning_size_pt: float = 12.0
    row_height_cm: float = 1.2
    # 排版方式：columns = 报纸式分栏（默认），table = 每词一行的两列表格
    layout: str = LAYOUT_COLUMNS
    #: 分栏排版时的栏数
    column_count: int = 3
    #: 是否横向页面。分栏时默认横向——A4 纵向三等分后每栏只有 5.3cm，
    #: 16pt 的藏文加释义会很挤；横向有 8.4cm，读起来舒服得多。
    landscape: bool = True

    @property
    def order_label(self) -> str:
        return ORDER_LABELS.get(self.order, "顺序")

    @property
    def meaning_visible(self) -> bool:
        """释义是否真的写出来（用于自测时可以只留藏文）。"""
        return self.include_meaning and not self.hide_meaning

    @property
    def table_column_count(self) -> int:
        """表格排版时的列数：包含释义是两列，否则只留藏文一列。"""
        return 2 if self.include_meaning else 1

    @property
    def use_columns(self) -> bool:
        return self.layout == LAYOUT_COLUMNS


# ---------------------------------------------------------------------------
# 排序 / 洗牌
# ---------------------------------------------------------------------------
def apply_order(rows: Sequence, options: ExportOptions) -> list:
    """按导出设置排列词条顺序。

    * 顺序导出：保持传入顺序（调用方按创建时间升序取出，即录入顺序）。
    * 乱序导出：洗牌；填了随机种子则结果可复现，留空则每次不同。
    """
    items = list(rows)
    if options.order != ORDER_RANDOM:
        return items

    seed_text = (options.seed or "").strip()
    if not seed_text:
        random.shuffle(items)
        return items

    # 纯数字种子转成 int，方便用户用 1 / 2 / 3 这样简单复现
    try:
        seed: object = int(seed_text)
    except ValueError:
        seed = seed_text
    random.Random(seed).shuffle(items)
    return items


# ---------------------------------------------------------------------------
# 生成文档
# ---------------------------------------------------------------------------
def _filename_safe(text: str) -> str:
    """去掉文件名里不允许的字符。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text).strip(" ._")
    return cleaned or "藏语词汇"


#: XML / .docx 里不允许出现的控制字符
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _safe_text(text: str) -> str:
    """把要写进文档的文本清洗成 XML 能接受的内容。

    数据写入时（models._prepare_word）已经过滤过一次，这里再兜一道：
    老的数据库文件、别人手工改过的备份都可能带着控制字符，
    一旦传进 lxml 就会抛 ValueError，让整个导出失败——而列表页看起来一切正常，
    用户完全不知道问题出在哪。导出这条路不能因为一个字节就整份失败。
    """
    if not text:
        return ""
    return _XML_ILLEGAL.sub("", str(text))


def build_filename(options: ExportOptions, today: date | None = None) -> str:
    """导出文件名：包含「顺序」/「乱序」以及日期。"""
    base = _filename_safe(options.title) if options.title.strip() else "藏语词汇"
    day = (today or date.today()).strftime("%Y%m%d")
    return f"{base}_{options.order_label}_{day}.docx"


#: OOXML 对 sectPr（节属性）子元素的顺序同样有严格要求。
#: w:cols 必须排在 w:pgMar 之后、w:formProt 之前，否则 Word 会认为文件损坏。
_SECTPR_ORDER: tuple[str, ...] = (
    "w:footnotePr", "w:endnotePr", "w:type", "w:pgSz", "w:pgMar", "w:paperSrc",
    "w:pgBorders", "w:lnNumType", "w:pgNumType", "w:cols", "w:formProt",
    "w:vAlign", "w:noEndnote", "w:titlePg", "w:textDirection", "w:bidi",
    "w:rtlGutter", "w:docGrid", "w:printerSettings",
)


def _set_or_insert(sectPr, tag: str):
    """取出（或按 OOXML 规定顺序新建）sectPr 下的某个子元素。"""
    element = sectPr.find(qn(tag))
    if element is not None:
        return element

    element = OxmlElement(tag)
    position = _SECTPR_ORDER.index(tag)
    for child in sectPr:
        child_tag = "w:" + child.tag.split("}")[-1]
        if child_tag in _SECTPR_ORDER and _SECTPR_ORDER.index(child_tag) > position:
            child.addprevious(element)
            break
    else:
        sectPr.append(element)
    return element


def _setup_page(section, *, landscape: bool) -> None:
    """设置一个节的纸张与页边距。

    横向时页边距收窄一些——分栏已经吃掉了不少横向空间，
    再留 2.5cm 的边距就太浪费了。
    """
    if landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(29.7)
        section.page_height = Cm(21.0)
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)
        section.top_margin = Cm(1.8)
        section.bottom_margin = Cm(1.8)
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)


def _set_columns(section, count: int) -> None:
    """把一节改成 ``count`` 栏（报纸式分栏）。"""
    cols = _set_or_insert(section._sectPr, "w:cols")
    cols.set(qn("w:num"), str(count))
    cols.set(qn("w:equalWidth"), "1")
    cols.set(qn("w:space"), str(COLUMN_SPACING_TWIPS))
    cols.set(qn("w:sep"), "0")   # 不在栏之间画分隔线，保持纸面干净


def _add_footer_page_numbers(document: Document) -> None:
    """页脚居中显示「第 X 页 / 共 Y 页」。"""
    for section in document.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 清掉模板里可能残留的空 run
        for run in list(paragraph.runs):
            run._element.getparent().remove(run._element)

        prefix = paragraph.add_run("第 ")
        _style_run(prefix, size_pt=10.5)
        _add_field(paragraph, "PAGE")
        middle = paragraph.add_run(" 页 / 共 ")
        _style_run(middle, size_pt=10.5)
        _add_field(paragraph, "NUMPAGES")
        suffix = paragraph.add_run(" 页")
        _style_run(suffix, size_pt=10.5)


def _add_title(document: Document, title: str) -> None:
    text = _safe_text(title).strip()
    if not text:
        return
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(text)
    _style_run(run, size_pt=18, bold=True, ascii_font=CHINESE_FONT, cs_font=CHINESE_FONT)
    paragraph.paragraph_format.space_after = Pt(12)


def _set_column_widths(table: Table, widths_cm: Sequence[float]) -> None:
    """固定列宽。必须逐单元格设置，Word 才会严格按此排版。"""
    table.autofit = False
    for row in table.rows:
        for cell, width in zip(row.cells, widths_cm):
            cell.width = Cm(width)


def _add_tibetan_run(paragraph, text: str, options: ExportOptions, *, size_pt=None):
    """往段落里追加一段藏文：复杂文本字体 + 加粗。

    一律加粗是为了打印——藏文笔画细、字身又小，不加重的话打印出来发虚。
    藏文字体大多没有真正的粗体字重，Word 会做合成加粗，效果是「更实」而不是
    「更粗」，正好是想要的效果。
    """
    run = paragraph.add_run(_safe_text(text))
    _style_run(
        run,
        size_pt=size_pt if size_pt is not None else options.tibetan_size_pt,
        bold=True,
        ascii_font=options.tibetan_font,
        east_asia_font=options.tibetan_font,
        cs_font=options.tibetan_font,   # 藏文走的是复杂文本字体
    )
    return run


def _add_meaning_run(paragraph, text: str, options: ExportOptions):
    run = paragraph.add_run(_safe_text(text))
    _style_run(run, size_pt=options.meaning_size_pt)
    return run


def _fill_table(table: Table, rows: Sequence, options: ExportOptions) -> None:
    """把词条逐行写进表格。"""
    for index, word in enumerate(rows):
        cells = table.rows[index].cells

        # 藏文单元格
        tibetan_cell = cells[0]
        tibetan_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        tibetan_paragraph = tibetan_cell.paragraphs[0]
        tibetan_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _add_tibetan_run(tibetan_paragraph, word.tibetan, options)

        # 释义单元格（包含释义时才存在）
        if options.table_column_count == 2:
            meaning_cell = cells[1]
            meaning_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            meaning_paragraph = meaning_cell.paragraphs[0]
            meaning_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            if options.meaning_visible and word.meaning:
                _add_meaning_run(meaning_paragraph, word.meaning, options)


def _column_tab_cm(landscape: bool, count: int) -> float:
    """算出藏文与释义之间的制表位位置。

    栏越窄，制表位越往左收——不然 4 栏时释义只剩三厘米，全在换行。
    """
    spacing_cm = COLUMN_SPACING_TWIPS / 567          # twips -> cm，约 0.5cm
    content = _content_width_cm(landscape)
    usable = (content - spacing_cm * (count - 1)) / max(count, 1)
    return round(min(COLUMN_TAB_CM, max(MIN_COLUMN_TAB_CM, usable * COLUMN_TAB_RATIO)), 2)


def _fill_columns(document: Document, rows: Sequence, options: ExportOptions) -> None:
    """把词条按「一行一个词」排进分栏正文。

    每个词条是一个段落：藏文 + 制表符 + 释义。制表位见 :func:`_column_tab_cm`，
    配悬挂缩进，这样释义太长换行时会跟释义左对齐、不会缩回藏文底下。
    """
    tab_cm = _column_tab_cm(options.landscape, options.column_count)

    for word in rows:
        paragraph = document.add_paragraph()
        fmt = paragraph.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(3)
        fmt.left_indent = Cm(tab_cm)
        fmt.first_line_indent = Cm(-tab_cm)     # 负值 -> w:hanging，悬挂缩进
        fmt.tab_stops.add_tab_stop(Cm(tab_cm))

        # 分栏时用同一个字号：栏宽 8 厘米出头，放得下 20 磅的藏文；
        # 之前会在分栏时收小 2 磅，加上藏文字形本来就小，打印出来太淡了
        _add_tibetan_run(paragraph, word.tibetan, options)

        if options.meaning_visible and word.meaning:
            paragraph.add_run("\t")
            _add_meaning_run(paragraph, word.meaning, options)


def _add_empty_hint(document: Document) -> None:
    hint = document.add_paragraph()
    hint.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hint_run = hint.add_run("（没有符合条件的词条）")
    _style_run(hint_run, size_pt=12)


def _set_doc_properties(document: Document, options: ExportOptions, count: int) -> None:
    """文档属性也会写进 XML（docProps/core.xml），所以同样要清洗。"""
    title = _safe_text(options.title).strip()
    if title:
        document.core_properties.title = title
    document.core_properties.comments = (
        f"由「藏语词汇录入与导出工具」生成 · "
        f"{options.order_label}导出 · {LAYOUT_LABELS.get(options.layout, '分栏')} · "
        f"共 {count} 条"
    )


def _build_columns_document(rows: Sequence, options: ExportOptions) -> Document:
    """分栏排版：标题独占一栏宽度，正文按栏流动。

    Word 的「栏」是**节**的属性，所以标题必须和正文分属两节，
    否则标题会被塞进第一栏里。这里用「连续」分节符把两节接在同一页上。
    """
    document = Document()

    # 第一节：只有标题，单栏
    _setup_page(document.sections[0], landscape=options.landscape)
    _add_footer_page_numbers(document)
    _add_title(document, options.title)

    if not rows:
        # 没有内容就不必开分栏节了，否则「（没有符合条件的词条）」那句提示
        # 会被塞进一个很窄的栏里
        return document

    # 第二节：正文，多栏
    content = document.add_section(WD_SECTION.CONTINUOUS)
    _setup_page(content, landscape=options.landscape)
    _set_columns(content, options.column_count)
    _fill_columns(document, rows, options)
    return document


def _build_table_document(rows: Sequence, options: ExportOptions) -> Document:
    """表格排版：每行一个词条，左列藏文、右列释义。"""
    document = Document()
    _setup_page(document.sections[0], landscape=options.landscape)
    _add_footer_page_numbers(document)
    _add_title(document, options.title)

    if not rows:
        # 不要生成一张空表格，否则导出「空表」会多出一个空框
        return document

    table = document.add_table(rows=len(rows), cols=options.table_column_count)
    table.style = "Table Grid"   # 带边框的网格样式，打印后便于阅读

    # 加大行高：AT_LEAST 保证内容多时行仍可自动变高
    for row in table.rows:
        row.height = Cm(options.row_height_cm)
        row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST

    content_width = _content_width_cm(options.landscape)
    if options.table_column_count == 2:
        tibetan_width = min(TIBETAN_COLUMN_CM, content_width / 2)
        widths = (tibetan_width, content_width - tibetan_width)
    else:
        widths = (content_width,)
    _set_column_widths(table, widths)

    _fill_table(table, rows, options)
    return document


def _content_width_cm(landscape: bool) -> float:
    """可用正文宽度（页面宽度减去左右页边距）。"""
    if landscape:
        return 29.7 - 1.8 * 2
    return 21.0 - 2.5 * 2


def build_document(rows: Sequence, options: ExportOptions) -> Document:
    """根据词条与导出设置生成 Word 文档对象。"""
    if options.use_columns:
        document = _build_columns_document(rows, options)
    else:
        document = _build_table_document(rows, options)

    _set_doc_properties(document, options, len(rows))

    if not rows:
        _add_empty_hint(document)
    return document


def export_to_bytes(rows: Sequence, options: ExportOptions) -> bytes:
    """生成 .docx 的二进制内容，供 Web 层直接流式返回。"""
    document = build_document(rows, options)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


#: 导出设置表单里可供选择的藏文字体
def font_choices() -> list[str]:
    return list(TIBETAN_FONT_CANDIDATES)
