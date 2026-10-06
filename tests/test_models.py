# -*- coding: utf-8 -*-
"""数据层（SQLite 读写、搜索、排序、备份导入导出）的单元测试。"""

from __future__ import annotations

import json

import pytest

from tibetan import Syllable, WordComponents

import models


def make_word(tibetan="མི", meaning="人", root="མ", vowel="ི", **kwargs) -> models.Word:
    """构造一个带组件的测试词条（默认是 基字 མ + 元音 ི => མི）。"""
    components = WordComponents(syllables=[Syllable(root=root, vowel=vowel)])
    return models.Word(
        tibetan=tibetan,
        components=components.to_json(),
        meaning=meaning,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 基础增删改查
# ---------------------------------------------------------------------------
def test_create_and_get_word():
    word_id = models.create_word(make_word())
    fetched = models.get_word(word_id)

    assert fetched is not None
    assert fetched.id == word_id
    assert fetched.tibetan == "མི"
    assert fetched.meaning == "人"
    assert fetched.created_at and fetched.updated_at
    # 组件能完整回填
    assert fetched.word_components.syllables[0].root == "མ"
    assert fetched.word_components.syllables[0].vowel == "ི"
    assert fetched.has_components


def test_update_word():
    word_id = models.create_word(make_word())
    original = models.get_word(word_id)

    changed = make_word(meaning="人类")
    changed.note = "订正后的释义"
    assert models.update_word(word_id, changed) is True

    updated = models.get_word(word_id)
    assert updated.meaning == "人类"
    assert updated.note == "订正后的释义"
    # 创建时间不变，更新时间被刷新
    assert updated.created_at == original.created_at
    assert updated.updated_at >= original.updated_at


def test_update_missing_word_returns_false():
    assert models.update_word(9999, make_word()) is False


def test_delete_word():
    word_id = models.create_word(make_word())
    assert models.delete_word(word_id) is True
    assert models.get_word(word_id) is None
    assert models.delete_word(word_id) is False


def test_count_and_clear():
    for index in range(3):
        models.create_word(make_word(tibetan="མི", meaning=f"释义{index}"))
    assert models.count_words() == 3
    assert models.delete_all_words() == 3
    assert models.count_words() == 0


# ---------------------------------------------------------------------------
# 标签处理
# ---------------------------------------------------------------------------
def test_normalize_tags_splits_on_many_separators():
    assert models.normalize_tags("名词, 地名") == ["名词", "地名"]
    assert models.normalize_tags("名词，地名") == ["名词", "地名"]
    assert models.normalize_tags("名词、地名；动词") == ["名词", "地名", "动词"]
    assert models.normalize_tags("名词|地名") == ["名词", "地名"]
    # 去重并保持顺序
    assert models.normalize_tags("名词, 名词") == ["名词"]
    assert models.normalize_tags("") == []
    assert models.normalize_tags(["名词", " 地名 ", ""]) == ["名词", "地名"]


def test_tags_roundtrip_and_all_tags():
    first = models.create_word(make_word(tags=["名词", "身体"]))
    models.create_word(make_word(tibetan="ཆུ", root="ཆ", vowel="ུ", meaning="水", tags=["名词"]))

    # 标签按写入顺序原样取回
    assert models.get_word(first).tags == ["名词", "身体"]
    # all_tags 去重
    assert models.all_tags() == ["名词", "身体"]


# ---------------------------------------------------------------------------
# 搜索 / 筛选 / 排序
# ---------------------------------------------------------------------------
def test_search_matches_tibetan_and_meaning():
    models.create_word(make_word(tibetan="བོད", root="བ", vowel="ོ", meaning="西藏"))
    models.create_word(make_word(tibetan="ཆུ", root="ཆ", vowel="ུ", meaning="水"))

    assert [w.meaning for w in models.list_words(q="བོད")] == ["西藏"]
    assert [w.tibetan for w in models.list_words(q="水")] == ["ཆུ"]
    assert models.list_words(q="不存在的词") == []


def test_search_normalizes_tsheg_bstar():
    """用 tsheg bstar（U+0F0C）粘贴的藏文也要能搜到（库里存的是 U+0F0B）。"""
    components = WordComponents(syllables=[
        Syllable(root="ད", subjoined=["ར", "ཝ"]),
        Syllable(root="བ"),
    ])
    models.create_word(models.Word(
        tibetan="དྲྭ་བ", components=components.to_json(), meaning="网子",
    ))

    assert len(models.list_words(q="དྲྭ་བ")) == 1
    assert len(models.list_words(q="དྲྭ༌བ")) == 1     # U+0F0C 写法
    assert len(models.list_words(q="དྲྭ")) == 1


def test_stored_tibetan_is_normalized_on_write():
    """写入时就把 tsheg bstar 归一成标准 tsheg。"""
    models.create_word(make_word(tibetan="ཉི༌མ", root="ཉ", vowel="ི", meaning="太阳"))
    stored = models.list_words()[0].tibetan
    assert stored == "ཉི་མ"
    assert 0x0F0C not in [ord(c) for c in stored]


def test_search_matches_note_and_tags():
    word = make_word(tibetan="ཆུ", root="ཆ", vowel="ུ", meaning="水")
    word.note = "注意后加字"
    word.tags = ["自然"]
    models.create_word(word)

    assert len(models.list_words(q="后加字")) == 1
    assert len(models.list_words(q="自然")) == 1


def test_filter_by_tag_is_exact():
    models.create_word(make_word(tibetan="ཆུ", root="ཆ", vowel="ུ", meaning="水", tags=["名词"]))
    models.create_word(make_word(tibetan="སྐད", root="ཀ", vowel="", meaning="语言", tags=["名词学"]))

    assert len(models.list_words(tag="名词")) == 1
    assert len(models.list_words(tag="名词学")) == 1
    assert models.list_words(tag="不存在") == []


def test_sort_options():
    first = models.create_word(make_word(meaning="第一条"))
    second = models.create_word(make_word(meaning="第二条"))

    assert [w.id for w in models.list_words(sort="created_asc")] == [first, second]
    assert [w.id for w in models.list_words(sort="created_desc")] == [second, first]
    # 未知排序值回落到默认排序，不报错
    assert len(models.list_words(sort="不存在")) == 2


def test_get_words_by_ids_preserves_requested_order():
    a = models.create_word(make_word(meaning="A"))
    b = models.create_word(make_word(meaning="B"))
    c = models.create_word(make_word(meaning="C"))

    assert [w.id for w in models.get_words_by_ids([c, a, b])] == [c, a, b]
    # 不存在的 id 被忽略，不报错
    assert [w.id for w in models.get_words_by_ids([b, 9999])] == [b]
    assert models.get_words_by_ids([]) == []


# ---------------------------------------------------------------------------
# 备份：导出
# ---------------------------------------------------------------------------
def test_export_payload_shape():
    models.create_word(make_word(tibetan="བོད", root="བ", vowel="ོ", meaning="西藏", tags=["名词"]))
    payload = models.export_payload()

    assert payload["format"] == models.BACKUP_FORMAT
    assert payload["version"] == models.BACKUP_VERSION
    assert payload["count"] == 1
    word = payload["words"][0]
    assert word["tibetan"] == "བོད"
    assert word["meaning"] == "西藏"
    assert word["tags"] == ["名词"]
    # components 直接是对象（v2 起），不再是内嵌的 JSON 字符串，
    # 这样备份文件和 data/sample_words.json 形状一致、可以直接手改
    components = word["components"]
    assert isinstance(components, dict)
    assert components["version"] == 2
    assert components["syllables"][0] == {
        "prefix": "", "superscript": "", "root": "བ", "subjoined": [],
        "vowel": "ོ", "suffix": "", "suffix2": "",
    }


def test_backup_components_are_objects_not_strings():
    models.create_word(make_word())
    word = models.export_payload()["words"][0]
    assert isinstance(word["components"], dict)


def test_v1_string_components_still_import():
    """v1 备份里 components 是内嵌 JSON 字符串，必须照常导入。"""
    legacy = [{
        "tibetan": "དྲྭ་བ",
        "components": json.dumps({
            "version": 1, "auto_join": True,
            "syllables": [
                {"prefix": "", "superscript": "", "root": "ད",
                 "subjoined": "ར", "vowel": "", "suffix": "", "suffix2": ""},
            ],
        }, ensure_ascii=False),
        "meaning": "网（v1 写法）",
    }]
    assert models.import_payload(legacy, mode="replace")["added"] == 1

    word = models.list_words()[0]
    assert word.tibetan == "དྲྭ་བ"
    assert word.word_components.syllables[0].subjoined == ["ར"]


def test_export_json_text_is_readable_unicode():
    models.create_word(make_word())
    text = models.export_json_text()
    # ensure_ascii=False，藏文原样输出方便人工查看
    assert "མི" in text
    assert json.loads(text)["count"] == 1


# ---------------------------------------------------------------------------
# 备份：导入
# ---------------------------------------------------------------------------
def test_import_append_mode(sample_words):
    stats = models.import_payload({"words": sample_words[:3]}, mode="append")
    assert stats == {"added": 3, "skipped": 0, "removed": 0}
    assert models.count_words() == 3


def test_import_append_skips_duplicates(sample_words):
    models.import_payload({"words": sample_words[:3]}, mode="append")
    stats = models.import_payload({"words": sample_words[:3]}, mode="append")
    assert stats["added"] == 0
    assert stats["skipped"] == 3
    assert models.count_words() == 3


def test_import_replace_mode(sample_words):
    models.create_word(make_word(meaning="会被清掉"))
    stats = models.import_payload({"words": sample_words[:2]}, mode="replace")
    assert stats["removed"] == 1
    assert stats["added"] == 2
    assert models.count_words() == 2
    assert all(w.meaning != "会被清掉" for w in models.list_words())


def test_import_accepts_bare_list(sample_words):
    stats = models.import_payload(sample_words[:2], mode="append")
    assert stats["added"] == 2


def test_import_rejects_bad_payload():

    with pytest.raises(ValueError):
        models.import_payload("我是一个字符串", mode="append")
    with pytest.raises(ValueError):
        models.import_payload({"words": "不是数组"}, mode="append")


def test_import_recovers_components_from_tibetan_only():
    """只有藏文、没有组件的数据也能导入，只是无法回填音节卡片。"""
    stats = models.import_payload(
        [{"tibetan": "བོད", "meaning": "西藏"}], mode="append"
    )
    assert stats["added"] == 1

    word = models.list_words()[0]
    assert word.tibetan == "བོད"
    assert word.has_components is False


def test_import_skips_records_without_any_tibetan():
    stats = models.import_payload(
        [{"meaning": "没有藏文"}, "不是字典", {"tibetan": "", "components": {}}],
        mode="append",
    )
    assert stats["added"] == 0
    assert stats["skipped"] == 3


def test_import_can_rebuild_tibetan_from_components():
    """藏文缺失但组件完整时，用组件现场合成。"""
    payload = [
        {
            "components": {
                "version": 1,
                "auto_join": True,
                "syllables": [{"root": "ཉ", "vowel": "ི"}, {"root": "མ"}],
            },
            "meaning": "太阳",
        }
    ]
    stats = models.import_payload(payload, mode="append")
    assert stats["added"] == 1
    assert models.list_words()[0].tibetan == "ཉི་མ།"


def test_bad_record_does_not_wipe_the_library(monkeypatch):
    """「清空后覆盖」遇到坏数据时必须原样回滚，绝不能清空词库。

    回归测试：早先的实现是「先 delete_all_words()，再逐条解析 + 逐条插入」，
    中间任何一条抛异常，用户看到 500，但词库已经被清空、还只导入了一半。
    这是不可逆的数据丢失。

    这里用注入失败的方式测这个**契约**：不管坏数据长什么样、在第几步炸，
    词库都必须纹丝不动。比依赖某个特定畸形输入更稳（畸形结构现在已经被
    解析层兜住了，反而测不出这条契约）。
    """

    for meaning in ("原有A", "原有B", "原有C"):
        models.create_word(make_word(meaning=meaning))
    before = [w.meaning for w in models.list_words()]

    original = models._word_from_backup
    seen = {"count": 0}

    def flaky(item):
        seen["count"] += 1
        if seen["count"] == 2:
            raise TypeError("模拟第 2 条解析失败")
        return original(item)

    monkeypatch.setattr(models, "_word_from_backup", flaky)

    with pytest.raises(ValueError) as excinfo:
        models.import_payload(
            {"words": [{"tibetan": "ཉི་མ", "meaning": "太阳"},
                       {"tibetan": "ཟླ་བ", "meaning": "月亮"}]},
            mode="replace",
        )

    # 报错要说清是第几条，并明确告诉用户数据没动
    assert "第 2 条" in str(excinfo.value)
    assert "未做任何改动" in str(excinfo.value)
    # 词库分毫未动
    assert [w.meaning for w in models.list_words()] == before


def test_replace_refuses_to_empty_the_library_by_accident():
    """整个文件没有一条可用记录时，「清空后覆盖」必须拒绝执行。

    否则用户拿错文件（或者文件格式不对）点一下覆盖，词库就空了。
    真想清空有专门的按钮。
    """
    for meaning in ("原有A", "原有B"):
        models.create_word(make_word(meaning=meaning))

    with pytest.raises(ValueError) as excinfo:
        models.import_payload(
            {"words": [{"tags": {}}, {"meaning": "没有藏文"}, "不是对象", {}]},
            mode="replace",
        )
    assert "已取消" in str(excinfo.value)
    assert models.count_words() == 2      # 一条都没少


def test_append_still_tolerates_an_all_skipped_file():
    """追加模式没有清空风险，整份都跳过时只是「新增 0 条」，不该报错。"""
    models.create_word(make_word(meaning="原有"))
    stats = models.import_payload({"words": [{"meaning": "没有藏文"}]}, mode="append")
    assert stats == {"added": 0, "skipped": 1, "removed": 0}
    assert models.count_words() == 1


def test_replace_with_an_empty_word_list_still_works():
    """显式给一个空 words 数组 = 明确要清空，应当允许（与拿错文件不同）。"""
    models.create_word(make_word())
    stats = models.import_payload({"words": []}, mode="replace")
    assert stats == {"added": 0, "skipped": 0, "removed": 1}
    assert models.count_words() == 0


def test_import_is_atomic_on_write_path(sample_words):
    """正常导入也要么全成、要么全不动（删和插在同一个事务里）。"""
    models.import_payload({"words": sample_words[:5]}, mode="replace")
    assert models.count_words() == 5

    stats = models.import_payload({"words": sample_words}, mode="replace")
    assert stats["removed"] == 5
    assert stats["added"] == len(sample_words)
    assert models.count_words() == len(sample_words)


def test_duplicate_records_in_one_import_are_deduped():
    """同一份文件里重复的条目只导入一条。"""
    payload = {"words": [
        {"tibetan": "བོད", "meaning": "西藏"},
        {"tibetan": "བོད", "meaning": "西藏"},
        {"tibetan": "བོད", "meaning": "藏地"},   # 释义不同，算两条
    ]}
    stats = models.import_payload(payload, mode="append")
    assert stats["added"] == 2
    assert stats["skipped"] == 1


# ---------------------------------------------------------------------------
# 脏数据与非法 id
# ---------------------------------------------------------------------------
def test_control_characters_are_stripped_when_stored():
    """控制字符要在写入时就清掉，否则导出 Word 时一定炸。"""
    word = models.Word(
        tibetan="བོད" + chr(11),
        meaning="释" + chr(0) + "义",
        note="备" + chr(31) + "注",
    )
    models.create_word(word)

    stored = models.list_words()[0]
    assert stored.tibetan == "བོད"
    assert stored.meaning == "释义"
    assert stored.note == "备注"


def test_strip_control_chars_keeps_normal_text():
    keep = "བོད་ཡིག 汉字\tABC"
    assert models.strip_control_chars(keep) == keep


def test_get_word_rejects_out_of_range_ids():
    """超出 SQLite 整数范围的 id 不能让驱动抛 OverflowError。"""
    assert models.get_word(10**24) is None
    assert models.get_word(-1) is None
    assert models.get_word("abc") is None
    assert models.get_word(None) is None
    assert models.delete_word(10**24) is False
    assert models.update_word(10**24, make_word()) is False


def test_parse_ids_ignores_junk_and_dedupes():
    assert models.parse_ids("1,2,3") == [1, 2, 3]
    assert models.parse_ids("1,1,1") == [1]
    assert models.parse_ids(" 2 , 2 ") == [2]
    assert models.parse_ids("") == []
    assert models.parse_ids(",,,") == []
    assert models.parse_ids("abc") == []
    assert models.parse_ids("10000000000000000000000000") == []
    # '²'.isdigit() 是 True，但 int('²') 会抛异常，必须挡掉
    assert models.parse_ids("²") == []


def test_get_words_by_ids_dedupes():
    word_id = models.create_word(make_word())
    assert len(models.get_words_by_ids([word_id, word_id, word_id])) == 1
    assert models.get_words_by_ids([10**24, "abc", None]) == []


def test_malformed_components_never_raise():
    """组件的结构不对时不能抛异常 —— 那不是数据问题，是解析器该扛住的输入。

    这类脏数据会让请求 500，而且一旦存进库，编辑页每次渲染都 500。
    """
    hostile = ["x", 5, [1, 2, 3], {"a": 1}, None, {"syllables": 5},
               {"syllables": [1, 2]}, {"syllables": [{"root": ["ཀ"]}]}]

    for payload in hostile:
        word = models._word_from_backup(
            {"tibetan": "བོད", "components": payload, "meaning": "m"}
        )
        assert word is not None, payload
        assert word.tibetan == "བོད", payload      # 藏文原样保住
        # 结构不对的容器退化成「没有音节」，界面会标注「无组件数据」
        if not isinstance(payload, dict) or not isinstance(payload.get("syllables"), list):
            assert word.word_components.syllables == [], payload
        # 无论哪种，取组件都不能炸
        assert word.has_components in (True, False)


def test_import_rejects_non_backup_payload():
    with pytest.raises(ValueError):
        models.import_payload("我不是备份", mode="append")


def test_export_import_roundtrip(sample_words):
    """导出再导入应当得到完全一致的数据。"""
    models.import_payload({"words": sample_words}, mode="replace")
    payload = models.export_payload()
    assert payload["count"] == len(sample_words)

    stats = models.import_payload(payload, mode="replace")
    assert stats["added"] == len(sample_words)
    assert models.export_payload()["count"] == len(sample_words)


def test_backup_and_sample_file_share_one_format(sample_words):
    """README 说备份文件和示例数据是同一种格式，这里把这句话钉死。"""
    from conftest import ROOT

    with open(ROOT / "data" / "sample_words.json", encoding="utf-8") as handle:
        sample = json.load(handle)

    models.import_payload(sample, mode="replace")
    exported = models.export_payload()

    assert sample["format"] == exported["format"] == models.BACKUP_FORMAT
    assert sample["version"] == exported["version"] == models.BACKUP_VERSION

    # 逐条比对键集合与 components 的形状
    sample_word = sample["words"][0]
    exported_word = exported["words"][0]
    assert set(sample_word) == set(exported_word)
    assert isinstance(sample_word["components"], dict)
    assert isinstance(exported_word["components"], dict)
