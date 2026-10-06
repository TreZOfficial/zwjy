# -*- coding: utf-8 -*-
"""
update_wordbank.py —— 把新的词库写进 index.html
==============================================

用法::

    python update_wordbank.py 期中词汇库.json          # 一键更新
    python update_wordbank.py 新词库.json --check      # 只检查，不动文件
    python update_wordbank.py 新词库.json --fix        # 组件与藏文对不上时，以组件为准修正
    python update_wordbank.py 新词库.json -o 别的.html  # 写到别的文件

脚本做的事：

1. 读入词库 JSON，归一化成 index.html 内嵌需要的形状；
2. 用本工程 ``tibetan.py`` 的合成器逐条校验「组件能合成出藏文」——
   这一步和网页里那份 JS 合成器是同一套规则，用来保证两边的答案一致；
3. 把结果写进 index.html 的 ``<script id="wordbank">`` 里（原子替换）；
4. 打印相对上一版的新增 / 删除 / 改动，以及哪些词因为内容变了导致
   **学习进度会重置**。

支持两种输入格式（自动识别）：

* 本工程导出的备份（``{"format": ..., "words": [...]}``，组件是
  ``{version, auto_join, trailing_shad, syllables: [...]}``）
* ``要求.md`` 里那种扁平写法（``components`` 直接是中文键名的字典，
  且下加字写的是**下加形式** ``ྲ``）——下加字会自动换算回正常形式

也可以直接给一个词条数组，两种格式混着放都行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from html import escape
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tibetan import (  # noqa: E402
    SHAD,
    Syllable,
    WordComponents,
    compose_word,
    normalize_text,
)

DEFAULT_HTML = BASE_DIR / "index.html"


def _make_output_safe() -> str | None:
    """让本脚本在 GBK 之类的控制台上也能跑完。

    Windows 的默认控制台编码是 GBK，印藏文会直接抛 UnicodeEncodeError ——
    一个「更新词库」的工具因为打印不出来就崩掉，太难用了。
    这里两步兜底：

    1. 把 stdout/stderr 的 errors 改成 ``replace``，保证再也不会因为编码崩；
    2. 返回当前编码，让 :func:`format_report` 知道能不能显示藏文
       （不能的话就只报数量，不硬印一串问号）。

    :return: 当前输出编码名；取不到就返回 None（当作能显示）
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    return getattr(sys.stdout, "encoding", None)


def _can_show(text: str) -> bool:
    """当前输出能不能显示出这段文字（主要是判断藏文）。"""
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return True
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _word_list(words: list[str], limit: int = 8) -> str:
    """把一组词排成一行；终端显示不了藏文时退化成只报数量。"""
    if not words:
        return ""
    if not _can_show("".join(words)):
        return f"（本终端显示不了藏文，共 {len(words)} 个）"
    preview = "、".join(words[:limit])
    if len(words) > limit:
        preview += f" 等 {len(words)} 个"
    return preview

#: 内嵌词库所在的 script 标签。整段会被替换掉，所以改格式时只要两边同步即可。
BANK_PATTERN = re.compile(
    r'(<script type="application/json" id="wordbank"[^>]*>)(.*?)(</script>)',
    re.DOTALL,
)

#: 扁平格式里组件的中文键名 -> 内部字段名
CN_KEYS = {
    "前加字": "prefix",
    "上加字": "superscript",
    "基字": "root",
    "下加字": "subjoined",
    "元音": "vowel",
    "后加字": "suffix",
    "再后加字": "suffix2",
}

#: 下加形式与正常形式之间固定的 0x50 偏移（和 tibetan.py 保持一致）
SUBJOINED_OFFSET = 0x50


class WordbankError(Exception):
    """词库有问题，直接终止、不改动任何文件。"""


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
def _to_normal_form(letter: str) -> str:
    """把下加形式换算回正常形式；本来就是正常形式就原样返回。

    扁平格式里下加字写的是 ``ྲ``（下加形式），而内部模型存的是 ``ར``，
    所以这一步是必须的——否则合成出来的藏文会多出一个下加字符。
    """
    if not letter:
        return ""
    code = ord(letter)
    if 0x0F90 <= code <= 0x0FBC:
        return chr(code - SUBJOINED_OFFSET)
    return letter


def _normalize_syllable(raw: Any) -> Syllable:
    """把一条音节（中文键名或英文字段名都行）归一成 Syllable。"""
    if not isinstance(raw, dict):
        return Syllable()

    data: dict[str, Any] = {}
    for key, value in raw.items():
        name = CN_KEYS.get(key, key)
        if name in CN_KEYS.values():
            data[name] = value

    subjoined = data.get("subjoined")
    if isinstance(subjoined, str):
        subjoined = [subjoined] if subjoined else []
    elif not isinstance(subjoined, list):
        subjoined = []

    return Syllable(
        prefix=str(data.get("prefix") or ""),
        superscript=str(data.get("superscript") or ""),
        root=str(data.get("root") or ""),
        subjoined=[_to_normal_form(str(x)) for x in subjoined if x],
        vowel=str(data.get("vowel") or ""),
        suffix=str(data.get("suffix") or ""),
        suffix2=str(data.get("suffix2") or ""),
    )


def _normalize_components(raw: Any) -> tuple[list[Syllable], bool | None, bool | None]:
    """把各种写法的 components 归一成 (音节列表, auto_join, trailing_shad)。

    后两项在扁平格式里没有，返回 None 表示「用默认值」。
    """
    if raw is None:
        return [], None, None

    # 本工程导出的形状：{version, auto_join, trailing_shad, syllables: [...]}
    if isinstance(raw, dict) and "syllables" in raw:
        items = raw.get("syllables")
        syllables = [_normalize_syllable(x) for x in items] if isinstance(items, list) else []
        return syllables, raw.get("auto_join"), raw.get("trailing_shad")

    # 单个音节：{前加字: ..., 基字: ...} 或 {prefix: ..., root: ...}
    if isinstance(raw, dict):
        return [_normalize_syllable(raw)], None, None

    # 多个音节
    if isinstance(raw, list):
        return [_normalize_syllable(x) for x in raw], None, None

    return [], None, None


def _make_id(tibetan: str, meaning: str, taken: set[str]) -> str:
    """给一个词生成稳定的 id。

    **必须稳定**：学习进度是按 id 存在浏览器里的，id 一变进度就重置。
    所以取藏文的哈希而不是数组下标——词库里插一条新词，其它词的 id 不受影响。
    改释义不会换 id（同一个词的释义订正不该丢进度）。
    只有藏文一模一样的同形词才会带上释义一起哈希。
    """
    key = normalize_text(tibetan)
    candidate = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    if candidate not in taken:
        return candidate
    # 撞了说明词库里有两个一模一样的藏文（同形不同义），把释义也算进去
    return hashlib.sha1((key + "|" + meaning).encode("utf-8")).hexdigest()[:10]


def _entry_from_any(raw: Any, taken_ids: set[str]) -> dict[str, Any]:
    """把一条记录（哪种格式都行）归一成内嵌用的词条。"""
    if not isinstance(raw, dict):
        raise WordbankError(f"词条必须是对象，收到 {type(raw).__name__}")

    tibetan = normalize_text(str(raw.get("tibetan") or ""))
    meaning = str(raw.get("meaning") or "").strip()
    if not tibetan:
        raise WordbankError(f"有一条词条没有 tibetan：{json.dumps(raw, ensure_ascii=False)[:120]}")

    syllables, auto_join, trailing_shad = _normalize_components(raw.get("components"))

    # 记录级的 auto_join / trailing_shad 作为兜底（本工程导出的格式两处都有）
    if auto_join is None:
        auto_join = bool(raw.get("auto_join", True))
    if trailing_shad is None:
        # 老数据没有这个字段：看它自己的藏文结尾有没有 །
        trailing_shad = tibetan.endswith(SHAD)

    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,，、;；|]", tags) if t.strip()]
    else:
        tags = [str(t).strip() for t in tags if str(t).strip()]

    entry_id = str(raw.get("id") or "").strip() or _make_id(tibetan, meaning, taken_ids)
    if entry_id in taken_ids:
        entry_id = _make_id(tibetan, meaning + "|" + entry_id, taken_ids)
    taken_ids.add(entry_id)

    return {
        "id": entry_id,
        "tibetan": tibetan,
        "meaning": meaning,
        "tags": tags,
        "autoJoin": bool(auto_join),
        "trailingShad": bool(trailing_shad),
        "components": [s.to_dict() for s in syllables],
    }


def load_wordbank(path: Path) -> list[dict[str, Any]]:
    """读入词库文件并归一化。任何一条不合规都会抛 WordbankError。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise WordbankError(f"找不到文件：{path}")
    except (UnicodeDecodeError, ValueError) as exc:
        raise WordbankError(f"不是合法的 UTF-8 JSON：{exc}")

    if isinstance(raw, dict):
        items = raw.get("words")
    elif isinstance(raw, list):
        items = raw
    else:
        raise WordbankError("词库顶层要么是词条数组，要么是含 words 数组的对象")

    if not isinstance(items, list):
        raise WordbankError("words 不是数组")
    if not items:
        raise WordbankError("词库里一条记录都没有，拒绝用空词库覆盖")

    taken: set[str] = set()
    return [_entry_from_any(item, taken) for item in items]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def check_entries(
    entries: list[dict[str, Any]], *, fix: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """逐条校验组件能不能合成出藏文。

    返回 (修正后的词条, 报告)。发现「组件合成结果 ≠ tibetan」时：

    * ``fix=False``（默认）—— 抛错，绝不静默写进去一份对不上的词库；
    * ``fix=True`` —— 以组件为准改写 tibetan，并记进报告。

    没有组件的词条允许存在（只影响「汉译藏」的提示，不影响判分），只做统计。
    """
    report = {
        "total": len(entries),
        "mismatched": [],      # [(tibetan, 组件合成结果)]
        "empty_components": 0,
        "no_shad": 0,
        "tags": {},
    }
    fixed: list[dict[str, Any]] = []

    for entry in entries:
        syllables = [Syllable.from_dict(s) for s in entry["components"]]
        if not syllables:
            report["empty_components"] += 1
        else:
            composed = compose_word(
                WordComponents(syllables=syllables, auto_join=entry["autoJoin"],
                               trailing_shad=entry["trailingShad"])
            )
            if composed != entry["tibetan"]:
                report["mismatched"].append((entry["tibetan"], composed))
                if not fix:
                    continue
                entry = dict(entry, tibetan=composed)

        if not entry["tibetan"].endswith(SHAD):
            report["no_shad"] += 1

        for tag in entry["tags"]:
            report["tags"][tag] = report["tags"].get(tag, 0) + 1
        fixed.append(entry)

    if report["mismatched"] and not fix:
        lines = [
            "以下词条的「组件」和「藏文」对不上，请先修数据，或加 --fix 以组件为准修正：",
        ]
        for tibetan, composed in report["mismatched"][:10]:
            lines.append(f"  tibetan={tibetan!r}  组件合成={composed!r}")
        if len(report["mismatched"]) > 10:
            lines.append(f"  …… 还有 {len(report['mismatched']) - 10} 条")
        raise WordbankError("\n".join(lines))

    return fixed, report


# ---------------------------------------------------------------------------
# 写回 HTML
# ---------------------------------------------------------------------------
def read_embedded(html: str) -> list[dict[str, Any]]:
    """读出 index.html 里当前内嵌的词库（读不出来就当空的）。"""
    match = BANK_PATTERN.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(2))
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("words") or []
    return data if isinstance(data, list) else []


def render_bank(entries: list[dict[str, Any]], generated_at: str, bank_id: str) -> str:
    """生成要写进 <script> 的那段 JSON。"""
    # 藏文和中文都保持原样（不转义成 \\uXXXX），文件才看得懂
    body = json.dumps(entries, ensure_ascii=False, indent=1)
    # 防止词条内容里出现 </script> 把标签提前闭合（JSON 里 \/ 是合法的）
    body = body.replace("</", "<\\/")
    attributes = (
        f' data-bank-id="{escape(bank_id, quote=True)}"'
        f' data-generated-at="{generated_at}"'
        f' data-count="{len(entries)}"'
    )
    opening = f'<script type="application/json" id="wordbank"{attributes}>'
    return f"{opening}\n{body}\n</script>"


def default_bank_id(json_path: Path) -> str:
    """默认的词库名：取词库文件名（不含扩展名）。

    这个名字会被网页拿去拼 localStorage 的键名，所以要求**同一套词库前后一致**，
    不然每次更新进度就对不上了。用文件名是最稳的——文件名不会因为改了几个词而变。

    为什么要带名字：localStorage 按**源**隔离，不区分路径。托管到 GitHub Pages 后
    同一账号下的多个词库页面属于同一个源，键名不区分就会互相覆盖进度。
    """
    return json_path.stem or "default"


def diff_banks(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, Any]:
    """比对两版词库，用来告诉用户这次更新动了什么。

    id 是从**藏文**算出来的，所以：

    * 释义/标签改了 -> id 不变，能认出来是同一个词（进度保留）；
    * **藏文改了 -> id 就变了**，只能表现为「删掉旧的 + 新增新的」，
      那个词的进度会重置。

    第二条没法从 id 直接看出来，所以额外做一次「按释义配对」的猜测：
    被删掉的词如果和某个新增的词释义相同，多半就是同一个词改写了藏文，
    在报告里点出来，免得用户以为是莫名其妙删了一个又加了一个。
    """
    old_by_id = {w.get("id"): w for w in old if isinstance(w, dict)}
    new_by_id = {w.get("id"): w for w in new}

    added = [i for i in new_by_id if i not in old_by_id]
    removed = [i for i in old_by_id if i not in new_by_id]

    # 同一个 id 但内容变了：只有释义/标签可能变（藏文变了 id 必变）
    changed = []
    for entry_id in new_by_id:
        if entry_id not in old_by_id:
            continue
        a, b = old_by_id[entry_id], new_by_id[entry_id]
        if (a.get("meaning"), a.get("tags")) != (b.get("meaning"), b.get("tags")):
            changed.append(entry_id)

    # 猜一猜哪些「删除」其实是「改了藏文」
    added_by_meaning: dict[str, list[str]] = {}
    for entry_id in added:
        added_by_meaning.setdefault(new_by_id[entry_id]["meaning"], []).append(entry_id)

    renamed = []      # [(旧藏文, 新藏文)]
    pure_removed = []
    for entry_id in removed:
        old_entry = old_by_id[entry_id]
        candidates = added_by_meaning.get(old_entry.get("meaning") or "", [])
        if len(candidates) == 1:
            renamed.append((old_entry.get("tibetan", "?"),
                            new_by_id[candidates[0]]["tibetan"]))
        else:
            pure_removed.append(entry_id)

    matched_added = {i for _, new_tib in renamed for i in added
                     if new_by_id[i]["tibetan"] == new_tib}

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "renamed": renamed,                     # 同一个词改了藏文（进度会重置）
        "pure_added": [i for i in added if i not in matched_added],
        "pure_removed": pure_removed,
        "new_by_id": new_by_id,
        "old_by_id": old_by_id,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def update(html_path: Path, json_path: Path, *, fix: bool = False,
           check_only: bool = False, generated_at: str | None = None,
           bank_id: str | None = None) -> dict[str, Any]:
    """把词库写进 HTML。返回一份报告，供调用方（和测试）使用。"""
    if not html_path.exists():
        raise WordbankError(f"找不到 HTML：{html_path}")

    html = html_path.read_text(encoding="utf-8")
    if not BANK_PATTERN.search(html):
        raise WordbankError(
            f"{html_path.name} 里找不到 <script id=\"wordbank\"> 标签，无法写入"
        )

    entries = load_wordbank(json_path)
    entries, report = check_entries(entries, fix=fix)

    old = read_embedded(html)
    changes = diff_banks(old, entries)

    stamp = generated_at
    if stamp is None:
        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    report.update({
        "html": str(html_path),
        "json": str(json_path),
        "generated_at": stamp,
        "bank_id": bank_id or default_bank_id(json_path),
        "old_count": len(old),
        "new_count": len(entries),
        "added": len(changes["added"]),
        "removed": len(changes["removed"]),
        "changed": len(changes["changed"]),
        "renamed": changes["renamed"],
        "added_words": [changes["new_by_id"][i]["tibetan"] for i in changes["pure_added"]],
        "removed_words": [
            changes["old_by_id"][i].get("tibetan", "?") for i in changes["pure_removed"]
        ],
        "changed_words": [
            changes["new_by_id"][i]["tibetan"] for i in changes["changed"]
        ],
        "check_only": check_only,
    })

    if check_only:
        return report

    new_block = render_bank(entries, stamp, bank_id or default_bank_id(json_path))
    new_html = BANK_PATTERN.sub(lambda m: new_block, html, count=1)

    # 原子替换：先写临时文件再改名，中途出错不会留下半个文件
    tmp = html_path.with_suffix(html_path.suffix + ".tmp")
    tmp.write_text(new_html, encoding="utf-8")
    os.replace(tmp, html_path)

    return report


def format_report(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"词库文件：{report['json']}")
    lines.append(f"词库名：{report['bank_id']}（学习进度按它存在浏览器里）")
    lines.append(f"目标页面：{report['html']}")
    lines.append(f"词条数量：{report['old_count']} -> {report['new_count']}")
    lines.append("")

    if report["mismatched"]:
        lines.append(f"⚠ 有 {len(report['mismatched'])} 条组件与藏文对不上，已按 --fix 以组件为准修正")

    lines.append(f"新增 {report['added']} 条，删除 {report['removed']} 条，改动 {report['changed']} 条")

    if report["added_words"]:
        lines.append(f"  新增：{_word_list(report['added_words'])}")
    if report["removed_words"]:
        lines.append(f"  删除：{_word_list(report['removed_words'])}")

    if report["changed_words"]:
        # 释义/标签改动不影响 id，所以这些词的进度是保住的
        lines.append(f"  内容改动（释义或标签，学习进度保留）：{_word_list(report['changed_words'])}")

    if report["renamed"]:
        pairs = report["renamed"]
        lines.append(f"  藏文改写 {len(pairs)} 条（**这些词的进度会重置**）：")
        if not _can_show("".join(old + new for old, new in pairs)):
            lines.append("    （本终端显示不了藏文）")
        else:
            for old, new in pairs[:8]:
                lines.append(f"    {old}  ->  {new}")

    if report["removed"] or report["added"]:
        lines.append("")
        lines.append("小提示：进度是按「藏文」算出来的编号存的，所以改写一个词的藏文，"
                     "在数据上就等于「删掉旧的、新增一个」——那个词要重新背。")

    lines.append("")
    if report["empty_components"]:
        lines.append(f"提醒：有 {report['empty_components']} 条没有组件，"
                     f"「汉译藏」模式下给不了组件提示（不影响判分）")
    if report["no_shad"]:
        lines.append(f"提醒：有 {report['no_shad']} 条词尾没有 །")

    tags = report.get("tags") or {}
    if tags:
        lines.append("按标签：" + "、".join(f"{k} {v}" for k, v in sorted(tags.items())))

    lines.append("")
    lines.append("（只检查，未写入）" if report["check_only"] else "✓ 已写入")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把新的词库 JSON 写进记忆工具的 index.html",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("wordbank", type=Path, help="新的词库 JSON（本工程导出的备份即可）")
    parser.add_argument("-o", "--html", type=Path, default=DEFAULT_HTML,
                        help=f"要更新的 HTML，默认 {DEFAULT_HTML.name}")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    parser.add_argument("--fix", action="store_true",
                        help="组件与藏文对不上时，以组件为准修正藏文（默认是报错退出）")
    parser.add_argument("--bank-id", default=None,
                        help="词库名，网页用它区分不同词库的学习进度（默认取词库文件名）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _make_output_safe()
    args = parse_args(argv)
    try:
        report = update(args.html, args.wordbank, fix=args.fix,
                        check_only=args.check, bank_id=args.bank_id)
    except WordbankError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
