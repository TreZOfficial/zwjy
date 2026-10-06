# -*- coding: utf-8 -*-
"""
models.py —— 数据模型与 SQLite 读写层
=====================================

使用标准库 ``sqlite3``，不引入 ORM，方便本地单机运行与备份。

数据库文件默认放在 ``data/words.db``，可以用环境变量 ``ZWJY_DB`` 覆盖
（测试里就是这么隔离数据库的）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from tibetan import SHAD, WordComponents, compose_word, normalize_text

# ---------------------------------------------------------------------------
# 路径与连接
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "words.db"


def db_path() -> Path:
    """当前使用的数据库文件路径。每次调用都读环境变量，方便测试切换。"""
    override = os.environ.get("ZWJY_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def get_connection() -> sqlite3.Connection:
    """打开一个连接。调用方负责关闭（各函数内部已用 with 管理）。"""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # 打开外键约束（当前没有关联表，留着以备后续扩展）
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """建表。幂等，可重复调用。"""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS words (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tibetan     TEXT    NOT NULL,              -- 合成后的藏文词语
                components  TEXT    NOT NULL DEFAULT '{}', -- 原始组件 JSON
                meaning     TEXT    NOT NULL DEFAULT '',   -- 中文释义
                note        TEXT    NOT NULL DEFAULT '',   -- 备注
                tags        TEXT    NOT NULL DEFAULT '[]', -- 标签，JSON 数组
                auto_join   INTEGER NOT NULL DEFAULT 1,    -- 是否用 ་ 连接音节
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_words_tibetan ON words(tibetan)")
        conn.commit()


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Word:
    """一个词条。"""

    tibetan: str
    components: str = "{}"          # 组件 JSON 文本
    meaning: str = ""
    note: str = ""
    tags: list[str] = field(default_factory=list)
    auto_join: bool = True
    created_at: str = ""
    updated_at: str = ""
    id: int | None = None

    # -- 组件结构（懒解析） ------------------------------------------------
    @property
    def word_components(self) -> WordComponents:
        return WordComponents.from_json(self.components)

    # -- 展示辅助 ----------------------------------------------------------
    @property
    def tags_text(self) -> str:
        """标签的逗号分隔文本，供输入框回填。"""
        return "、".join(self.tags)

    @property
    def has_components(self) -> bool:
        """是否存有可回填的音节组件。"""
        return bool(self.word_components.meaningful_syllables())

    @property
    def syllable_count(self) -> int:
        return len(self.word_components.meaningful_syllables())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tibetan": self.tibetan,
            "components": self.components,
            "meaning": self.meaning,
            "note": self.note,
            "tags": self.tags,
            "auto_join": self.auto_join,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_to_word(row: sqlite3.Row) -> Word:
    """把数据库行转成 Word 对象。"""
    try:
        tags = json.loads(row["tags"])
    except (TypeError, ValueError):
        tags = []
    if not isinstance(tags, list):
        tags = []
    return Word(
        id=row["id"],
        tibetan=row["tibetan"],
        components=row["components"] or "{}",
        meaning=row["meaning"] or "",
        note=row["note"] or "",
        tags=[str(t) for t in tags if str(t).strip()],
        auto_join=bool(row["auto_join"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def now_text() -> str:
    """当前时间，格式 ``YYYY-MM-DD HH:MM:SS``。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


#: LIKE 模式里的转义字符。SQL 语句里要配套写 ``ESCAPE '\'``。
LIKE_ESCAPE = "\\"


def _escape_like(text: str) -> str:
    """转义 LIKE 模式里的通配符，让用户输入的 ``%`` / ``_`` 按字面匹配。

    注意先转义反斜杠本身，否则用户输入 ``\\%`` 会被二次转义成错误的模式。
    """
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


#: XML（以及 Word 的 .docx）不允许出现的控制字符。
#: 这些字符在文本里没有任何意义，但从别处复制或手工改备份时可能带进来；
#: 一旦存进库，导出 Word 时 python-docx/lxml 会直接抛异常，整个导出失败。
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_chars(text: str) -> str:
    """去掉 XML 不接受的控制字符（保留 \\t \\n \\r 是安全的，但也一并去掉，
    因为词条、释义、备注都是单行文本）。"""
    return _CONTROL_CHARS.sub("", text)


#: SQLite 的整数上限。超出范围的 id 传给驱动会抛 OverflowError。
MAX_SQLITE_INT = 2**63 - 1


def valid_id(value: object) -> int | None:
    """把外部传来的 id 收敛成一个能安全交给 SQLite 的整数。

    非法（非数字、负数、超出 SQLite 整数范围）时返回 ``None``，
    调用方据此当成「找不到这条记录」，而不是让驱动抛异常变成 500。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        # 注意 '²'.isdigit() 也是 True，但 int('²') 会抛 ValueError，
        # 所以要先确认是 ASCII 数字
        if not text or not text.isascii() or not text.isdigit():
            return None
        number = int(text)
    return number if 0 <= number <= MAX_SQLITE_INT else None


def normalize_tags(raw: str | Iterable[str]) -> list[str]:
    """把用户输入的标签文本拆成去重后的列表。

    支持中英文逗号、顿号、分号、竖线作为分隔符。
    非字符串也非序列的输入（备份里写成数字、布尔之类）返回空列表，
    不抛异常——这里处理的是外部数据。
    """
    if isinstance(raw, str):
        text = raw
        for sep in ("，", "、", "；", ";", "|", "｜"):
            text = text.replace(sep, ",")
        items = text.split(",")
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []

    result: list[str] = []
    for item in items:
        value = str(item).strip()
        if value and value not in result:
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
#: 列表页可选的排序方式：键 -> (显示名, SQL ORDER BY 片段)
SORT_OPTIONS: dict[str, tuple[str, str]] = {
    "created_desc": ("创建时间（新→旧）", "created_at DESC, id DESC"),
    "created_asc": ("创建时间（旧→新，即录入顺序）", "created_at ASC, id ASC"),
    "updated_desc": ("更新时间（近→远）", "updated_at DESC, id DESC"),
    # 注意：这是按藏文字符串的 Unicode 码点排序，**不是**传统藏文词典的
    # 字母序（词典序按基字归类，忽略前加字与上加字）。要真正的词典序需要
    # 实现一套藏文排序键，目前没做，README 的已知限制里写明了。
    "tibetan_asc": ("藏文（按 Unicode 码点）", "tibetan ASC, id ASC"),
}
DEFAULT_SORT = "created_desc"


def list_words(
    q: str = "",
    tag: str = "",
    sort: str = DEFAULT_SORT,
) -> list[Word]:
    """按关键字与标签筛选词条并排序。

    :param q: 关键字，匹配藏文 / 释义 / 备注 / 标签
    :param tag: 标签名，精确匹配
    :param sort: :data:`SORT_OPTIONS` 中的键
    """
    sql = "SELECT * FROM words"
    where: list[str] = []
    params: list[Any] = []

    # 藏文关键字先归一：粘贴进来的 tsheg bstar（U+0F0C）要换成标准的 ་（U+0F0B），
    # 否则跟库里存的写法码点不同，会「明明有这个词却搜不到」
    keyword = normalize_text((q or "").strip())
    if keyword:
        # 用 LIKE 做简单的模糊匹配：藏文子串、释义、备注、标签都能命中。
        # LIKE 里 % 和 _ 是通配符，用户真搜这两个字符时（例如搜「_」找占位符）
        # 会意外命中全部词条，所以要转义，并在 SQL 末尾声明 ESCAPE 字符。
        like = f"%{_escape_like(keyword)}%"
        where.append(
            r"(tibetan LIKE ? ESCAPE '\' OR meaning LIKE ? ESCAPE '\'"
            r" OR note LIKE ? ESCAPE '\' OR tags LIKE ? ESCAPE '\')"
        )
        params.extend([like, like, like, like])

    tag_name = (tag or "").strip()
    if tag_name:
        # 标签存成 JSON 数组，这里用带引号的精确子串匹配，避免 "foo" 命中 "foobar"
        where.append("tags LIKE ? ESCAPE '\\'")
        params.append(f'%"{_escape_like(tag_name)}"%')

    if where:
        sql += " WHERE " + " AND ".join(where)

    order = SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])[1]
    sql += f" ORDER BY {order}"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_word(row) for row in rows]


def get_word(word_id: object) -> Word | None:
    safe_id = valid_id(word_id)
    if safe_id is None:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (safe_id,)).fetchone()
    return _row_to_word(row) if row else None


def get_words_by_ids(word_ids: Sequence[object]) -> list[Word]:
    """按 id 批量取词条，返回顺序与传入的 id 顺序一致（用于「手动勾选」导出）。

    非法的 id 直接忽略；重复的 id 会去重——否则同一个词会在导出里出现多次。
    """
    ids: list[int] = []
    for raw in word_ids:
        safe_id = valid_id(raw)
        if safe_id is not None and safe_id not in ids:
            ids.append(safe_id)
    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM words WHERE id IN ({placeholders})", ids
        ).fetchall()
    by_id = {row["id"]: _row_to_word(row) for row in rows}
    return [by_id[i] for i in ids if i in by_id]


def parse_ids(raw: str) -> list[int]:
    """把「1,2,3」这类逗号分隔的 id 串解析成合法 id 列表：忽略非法项、去重。

    去重是必须的——用户在 URL 里写 ``ids=1,1,1`` 会让同一个词在导出里出现三次。
    """
    result: list[int] = []
    for part in (raw or "").split(","):
        safe_id = valid_id(part)
        if safe_id is not None and safe_id not in result:
            result.append(safe_id)
    return result


def all_tags() -> list[str]:
    """返回词库里出现过的全部标签（去重、按拼音无关的字典序）。"""
    seen: list[str] = []
    with get_connection() as conn:
        rows = conn.execute("SELECT tags FROM words").fetchall()
    for row in rows:
        try:
            tags = json.loads(row["tags"])
        except (TypeError, ValueError):
            continue
        if isinstance(tags, list):
            for tag in tags:
                value = str(tag).strip()
                if value and value not in seen:
                    seen.append(value)
    return sorted(seen)


def count_words() -> int:
    with get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM words").fetchone()[0])


# ---------------------------------------------------------------------------
# 增删改
# ---------------------------------------------------------------------------
#: 新增词条的 SQL。导入时也要用同一份，保证两条写入路径完全一致。
_WORD_INSERT_SQL = """
    INSERT INTO words
        (tibetan, components, meaning, note, tags, auto_join, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _prepare_word(word: Word) -> Word:
    """写入前的字段整理：去空白、归一藏文、补时间戳。就地修改并返回。"""
    timestamp = now_text()
    word.tibetan = normalize_text(strip_control_chars((word.tibetan or "").strip()))
    word.meaning = strip_control_chars((word.meaning or "").strip())
    word.note = strip_control_chars((word.note or "").strip())
    word.created_at = word.created_at or timestamp
    word.updated_at = word.updated_at or timestamp
    return word


def _insert_values(word: Word) -> tuple:
    """把 Word 转成 ``_WORD_INSERT_SQL`` 需要的参数元组。"""
    return (
        word.tibetan,
        word.components or "{}",
        word.meaning,
        word.note,
        json.dumps(normalize_tags(word.tags), ensure_ascii=False),
        1 if word.auto_join else 0,
        word.created_at,
        word.updated_at,
    )


def create_word(word: Word) -> int:
    """新增词条，返回新记录的 id。"""
    _prepare_word(word)
    with get_connection() as conn:
        cursor = conn.execute(_WORD_INSERT_SQL, _insert_values(word))
        conn.commit()
        return int(cursor.lastrowid)


def update_word(word_id: int, word: Word) -> bool:
    """整体更新一个词条，返回是否命中记录。"""
    if valid_id(word_id) is None:
        return False
    _prepare_word(word)
    word.updated_at = now_text()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE words
               SET tibetan = ?, components = ?, meaning = ?, note = ?,
                   tags = ?, auto_join = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                word.tibetan,
                word.components or "{}",
                word.meaning,
                word.note,
                json.dumps(normalize_tags(word.tags), ensure_ascii=False),
                1 if word.auto_join else 0,
                now_text(),
                word_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_word(word_id: object) -> bool:
    safe_id = valid_id(word_id)
    if safe_id is None:
        return False
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM words WHERE id = ?", (safe_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_all_words() -> int:
    """清空全部词条，返回删除条数。"""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM words")
        conn.commit()
        return cursor.rowcount


# ---------------------------------------------------------------------------
# 备份：导出 / 导入 JSON
# ---------------------------------------------------------------------------
#: 备份文件的外层格式标识与版本。
#:
#: v1：每条的 ``components`` 是**内嵌的 JSON 字符串**（{"syllables": "..."} 这样
#:     嵌套一层转义，很难手改）
#: v2：``components`` 直接是**对象**，与 data/sample_words.json 形状一致，
#:     可以直接用文本编辑器改
#:
#: 导入时两种都能识别（见 :func:`_word_from_backup`），老备份不用转换。
BACKUP_FORMAT = "zwjy-tibetan-vocabulary"
BACKUP_VERSION = 2


def _word_to_backup(word: Word) -> dict[str, Any]:
    """把一条词条转成备份里的记录。

    ``components`` 写成对象而不是 JSON 字符串，这样备份文件和
    ``data/sample_words.json`` 是同一种形状，用户可以直接照着改。
    顺带把 v1 的字符串下加字归一成 v2 的列表写法。
    """
    data = word.to_dict()
    # 备份里不带 id：id 是本地数据库的自增值，导入时会被重新分配，
    # 写进备份只会让人误以为能靠它找回原编号。去掉后备份与示例数据完全同形。
    data.pop("id", None)

    record = WordComponents.from_json(word.components)
    if not record.meaningful_syllables():
        # 没有音节时组件里的 auto_join 是无意义的默认值，用记录级字段兜底
        record.auto_join = word.auto_join
    # 旧数据库里的组件没有 trailing_shad 字段，from_json 会给它默认值 true，
    # 但那条记录的藏文其实没有 །。按实际藏文推断，免得备份里写着一个
    # 跟内容对不上的开关（和导入时的处理保持对称）。
    if "trailing_shad" not in _raw_components_dict(word.components):
        record.trailing_shad = word.tibetan.endswith(SHAD)
    data["components"] = record.to_dict()
    return data


def _raw_components_dict(raw: Any) -> dict[str, Any]:
    """把组件（JSON 文本或已是字典）解析成字典；解析不了就返回空字典。

    用来判断某个字段在**原始数据里到底有没有**——这跟"取出来的值是多少"
    不一样，因为缺字段会被 :meth:`WordComponents.from_dict` 填上默认值。
    """
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def export_payload() -> dict[str, Any]:
    """生成备份用的 JSON 结构。"""
    words = list_words(sort="created_asc")
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": now_text(),
        "count": len(words),
        "words": [_word_to_backup(w) for w in words],
    }


def export_json_text() -> str:
    """备份 JSON 文本（缩进 2 空格、藏文原样，方便人工查看与 diff）。"""
    return json.dumps(export_payload(), ensure_ascii=False, indent=2)


def _word_from_backup(item: dict[str, Any]) -> Word | None:
    """把备份 JSON 里的一条记录还原成 Word。缺字段时用默认值补齐。"""
    if not isinstance(item, dict):
        return None

    tibetan = str(item.get("tibetan") or "").strip()
    components = item.get("components")

    # 组件缺失时退化成空组件：词条仍可正常显示与导出，只是无法回填音节卡片
    if isinstance(components, dict):
        components_text = json.dumps(components, ensure_ascii=False)
    elif isinstance(components, str) and components.strip():
        components_text = components
    else:
        components_text = "{}"

    raw_components = _raw_components_dict(components)
    record = WordComponents.from_json(components_text)

    if not tibetan:
        # 藏文缺失时，尝试用组件现场合成一次
        tibetan = compose_word(record).strip()
    if not tibetan:
        return None  # 既没有藏文也没有组件，这条记录无法使用

    # trailing_shad 是后加的字段，老备份里没有。这时**按现有藏文推断**：
    # 结尾本来就带 ། 的算开着，不带的算关掉。这样导入旧数据不会悄悄
    # 改变原有词条的写法（否则用户一保存就发现多出来一个 །）。
    if "trailing_shad" not in raw_components:
        record.trailing_shad = tibetan.endswith(SHAD)
        components_text = record.to_json()

    # auto_join 在备份里有**两处**：记录级的字段，以及组件 JSON 内部的同名字段。
    # 编辑页的音节卡片读的是组件里那份，所以只要组件里有音节就以组件为准，
    # 否则两者可能不一致（比如记录级写 true、组件里写 false）。
    if record.meaningful_syllables():
        auto_join = record.auto_join
    else:
        auto_join = bool(item.get("auto_join", True))
        record.auto_join = auto_join
        components_text = record.to_json()

    return Word(
        tibetan=tibetan,
        components=components_text,
        meaning=str(item.get("meaning") or "").strip(),
        note=str(item.get("note") or "").strip(),
        tags=normalize_tags(item.get("tags") or []),
        auto_join=auto_join,
        created_at=str(item.get("created_at") or "") or now_text(),
        updated_at=str(item.get("updated_at") or "") or now_text(),
    )


def import_payload(payload: Any, mode: str = "append") -> dict[str, int]:
    """导入备份数据。

    :param mode: ``append`` = 追加合并（跳过藏文+释义完全相同的重复项）；
                 ``replace`` = 先清空全部词条再导入。
    :return: ``{"added": 新增数, "skipped": 跳过数, "removed": 清空数}``
    :raises ValueError: 数据结构无法识别
    """
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("words")
    else:
        raise ValueError("无法识别的备份格式：既不是 JSON 数组，也不是包含 words 字段的对象。")

    if not isinstance(items, list):
        raise ValueError("备份文件里缺少 words 列表，或 words 不是数组。")

    # -- 第一阶段：把所有条目解析成 Word，**这一步不碰数据库** ------------------
    # 顺序很重要。「清空后覆盖」会先删库，如果边解析边写，一条坏数据就能让
    # 用户丢掉整个词库、还只导入了一半。所以先把全部条目解析完，解析失败就
    # 直接抛出，数据库保持原样。
    parsed: list[Word] = []
    skipped = 0
    for position, item in enumerate(items, start=1):
        try:
            word = _word_from_backup(item)
        except Exception as exc:  # 备份是外部文件，任何解析异常都要转成可读提示
            raise ValueError(
                f"第 {position} 条记录无法解析（{type(exc).__name__}: {exc}），"
                f"词库未做任何改动。"
            ) from exc
        if word is None:
            skipped += 1
            continue
        parsed.append(word)

    # 追加模式下，先取出已有词条做去重
    existing: set[tuple[str, str]] = set()
    if mode != "replace":
        for word in list_words():
            existing.add((word.tibetan, word.meaning))

    to_insert: list[Word] = []
    for word in parsed:
        _prepare_word(word)
        key = (word.tibetan, word.meaning)
        if mode != "replace" and key in existing:
            skipped += 1
            continue
        existing.add(key)
        to_insert.append(word)

    # 兜底护栏：文件里**有记录、但一条都导不进来**时，「清空后覆盖」会把词库清空，
    # 而这几乎不会是用户的意图（真想清空有专门的按钮）。宁可拒绝导入。
    # 注意 items 本身为空不算——那是一个明确的「清空词库」请求，应当放行。
    if mode == "replace" and items and not to_insert:
        raise ValueError(
            f"文件里 {len(items)} 条记录没有一条可以导入（都缺少藏文或无法解析）。"
            f"为避免把词库清空，本次导入已取消，词库未做任何改动。"
        )

    # -- 第二阶段：删 + 插在同一个事务里，要么全成，要么全不动 ------------------
    with get_connection() as conn:
        removed = conn.execute("DELETE FROM words").rowcount if mode == "replace" else 0
        if to_insert:
            conn.executemany(_WORD_INSERT_SQL, [_insert_values(w) for w in to_insert])
        conn.commit()

    return {"added": len(to_insert), "skipped": skipped, "removed": removed}
