# -*- coding: utf-8 -*-
"""Web 层（路由、表单、下载）的集成测试。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from urllib.parse import unquote

import pytest
from docx import Document

import models
from tibetan import Syllable, WordComponents

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def table_body(html: str) -> str:
    """只取出词条表格的 tbody。

    页面上的搜索框 placeholder 里也写着「བོད」「西藏」这类示例字，
    直接在整个 HTML 里查找会把 placeholder 误当成搜索结果。
    """
    match = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    return match.group(1) if match else ""


def components_json(*syllables) -> str:
    """把若干 (kwargs) 组成 WordComponents 的 JSON 文本。"""
    components = WordComponents(syllables=[Syllable(**kwargs) for kwargs in syllables])
    return components.to_json()


def form_payload(**overrides) -> dict:
    """一份合法的提交表单。"""
    payload = {
        "components_json": components_json({"root": "བ", "vowel": "ོ", "suffix": "ད"}),
        "tibetan": "",
        "meaning": "西藏",
        "note": "",
        "tags": "名词, 地名",
        "auto_join": "on",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
def test_index_renders_empty_state(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "词条列表" in response.text
    assert "词库还是空的" in response.text


def test_index_lists_words_with_tags(client):
    client.post("/words", data=form_payload())
    body = table_body(client.get("/").text)

    assert "བོད" in body
    assert "西藏" in body
    assert "名词" in body


def test_index_search_treats_like_wildcards_literally(client):
    """搜 `%` / `_` 不能变成「匹配全部」。

    这两个字符是 SQL LIKE 的通配符，不转义的话搜「_」会把整个词库都捞出来。
    """
    client.post("/words", data=form_payload())
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ཆ", "vowel": "ུ"}),
        tibetan="", meaning="水", tags="名词",
    ))

    for wildcard in ("%", "_", "%%", "西_"):
        body = table_body(client.get("/", params={"q": wildcard}).text)
        assert body == "", f"搜索 {wildcard!r} 不该命中任何词条"

    # 正常的子串搜索不受影响
    assert "西藏" in table_body(client.get("/", params={"q": "西"}).text)


def test_index_search_and_filter(client):
    client.post("/words", data=form_payload())
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ཆ", "vowel": "ུ"}),
        tibetan="", meaning="水", tags="名词",
    ))

    assert "西藏" not in table_body(client.get("/?q=水").text)
    assert "西藏" in table_body(client.get("/?q=བོད").text)
    assert "没有找到符合条件的词条" in client.get("/?q=不存在").text

    # 标签筛选：新增一个只属于「自然」标签的词条
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ར", "vowel": "ི"}),
        tibetan="", meaning="山", tags="自然",
    ))
    filtered = table_body(client.get("/?tag=地名").text)
    assert "西藏" in filtered
    assert "山" not in filtered


def test_new_word_page_renders_seven_selects_and_template(client):
    response = client.get("/words/new")
    assert response.status_code == 200
    for label in ["前加字", "上加字", "基字", "下加字", "元音", "后加字", "再后加字"]:
        assert label in response.text
    for component in ["prefix", "superscript", "root", "subjoined", "vowel", "suffix", "suffix2"]:
        assert f'data-component="{component}"' in response.text
    # 实时预览与「添加音节」入口
    assert 'id="preview"' in response.text
    assert 'id="btn-add-syllable"' in response.text


def test_edit_page_has_subjoined_stack_controls(client):
    """下加字槽要能叠层：默认一层，另有单层模板与「+ 加一层」按钮。"""
    response = client.get("/words/new")
    assert 'id="subjoined-row-template"' in response.text
    assert "btn-add-subjoined" in response.text
    assert "btn-remove-subjoined" in response.text
    assert 'class="subjoined-rows"' in response.text
    # 层数上限由服务端注入给 JS
    assert "const MAX_SUBJOINED = 3;" in response.text


def test_create_word_with_two_subjoined(client):
    """双下加字（གྲྭ）能通过表单存下来。"""
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ག", "subjoined": ["ར", "ཝ"]}),
        meaning="寺院；僧团", tags="名词",
    ))

    word = models.list_words()[0]
    assert word.tibetan == "གྲྭ"
    assert word.word_components.syllables[0].subjoined == ["ར", "ཝ"]
    assert "གྲྭ" in table_body(client.get("/").text)


def test_word_without_components_can_be_edited(client):
    """没有音节组件的词条必须能保存，否则编辑页是个死胡同。

    回归测试：这类词条（导入的纯藏文数据）在编辑页里音节卡片是空的，
    而前端原本一律拦下「卡片为空」的提交 —— 用户只想改一下释义都存不了，
    而且编辑页根本没有能输入藏文的地方。
    """
    models.import_payload([{"tibetan": "བོད", "meaning": "西藏"}], mode="replace")
    word = models.list_words()[0]
    assert not word.has_components

    page = client.get(f"/words/{word.id}/edit").text
    # 页面上有一个可直接编辑藏文的输入框，且预填了原值
    assert 'id="tibetan_text"' in page
    assert 'value="བོད"' in page

    # 前端提交时会把框里的内容放进 tibetan 隐藏域，组件则是空的
    client.post(f"/words/{word.id}", data={
        "components_json": '{"version": 2, "auto_join": true, "syllables": []}',
        "tibetan": "བོད་ཡིག",
        "meaning": "藏文",
        "note": "",
        "tags": "",
    })

    updated = models.get_word(word.id)
    assert updated.tibetan == "བོད་ཡིག"      # 只改藏文，没被清空
    assert updated.meaning == "藏文"
    assert updated.has_components is False


def test_word_with_components_has_no_text_fallback(client):
    """有组件的词条不该出现那个输入框——那样会绕开组件录入的设计。"""
    client.post("/words", data=form_payload())
    word = models.list_words()[0]

    page = client.get(f"/words/{word.id}/edit").text
    assert 'id="tibetan_text"' not in page


def test_edit_page_refills_two_subjoined(client):
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ཕ", "subjoined": ["ཡ", "ཝ"]}),
        meaning="预兆", tags="名词",
    ))
    word = models.list_words()[0]

    html = client.get(f"/words/{word.id}/edit").text
    raw = re.search(r"const INITIAL = (\{.*?\});", html, re.S).group(1)
    assert json.loads(raw)["syllables"][0]["subjoined"] == ["ཡ", "ཝ"]


def test_create_word_migrates_v1_string_subjoined(client):
    """v1 备份里的字符串写法通过表单提交时也要能正常存下来。"""
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ག", "subjoined": "ར"}),
        meaning="教师", tags="名词",
    ))
    word = models.list_words()[0]
    assert word.tibetan == "གྲ"
    assert word.word_components.syllables[0].subjoined == ["ར"]


def test_api_preview_accepts_subjoined_list(client):
    response = client.post("/api/preview", json={
        "auto_join": True,
        "syllables": [{"root": "ག", "subjoined": ["ར", "ཝ"]}],
    })
    body = response.json()
    assert body["tibetan"] == "གྲྭ།"
    assert body["issues"] == []


def test_api_preview_accepts_known_double_subjoined(client):
    """དྲྭ（དྲྭ་བ = 网）这类 基字+ྲ+ྭ 的双下加字不该被误报。"""
    response = client.post("/api/preview", json={
        "auto_join": True,
        "syllables": [{"root": "ད", "subjoined": ["ར", "ཝ"]}],
    })
    body = response.json()
    assert body["tibetan"] == "དྲྭ།"
    assert body["issues"] == []


def test_api_preview_warns_on_unusual_double_subjoined(client):
    response = client.post("/api/preview", json={
        "auto_join": True,
        "syllables": [{"root": "ག", "subjoined": ["ལ", "ཝ"]}],
    })
    body = response.json()
    assert body["tibetan"] == "གླྭ།"
    assert any("wa-zur" in i["message"] for i in body["issues"])


def test_edit_page_refills_components(client):
    client.post("/words", data=form_payload())
    word = models.list_words()[0]

    response = client.get(f"/words/{word.id}/edit")
    assert response.status_code == 200
    assert "const INITIAL" in response.text

    # 组件被回填进 INITIAL，前端据此还原音节卡片。
    # 注意 Jinja 的 tojson 会把非 ASCII 转成 \uXXXX 转义，所以要解析后再比。
    raw = re.search(r"const INITIAL = (\{.*?\});", response.text, re.S).group(1)
    initial = json.loads(raw)
    assert initial["syllables"] == [{
        "prefix": "", "superscript": "", "root": "བ", "subjoined": [],
        "vowel": "ོ", "suffix": "ད", "suffix2": "",
    }]
    assert initial["auto_join"] is True

    assert "西藏" in response.text


def test_edit_missing_word_redirects_home(client):
    response = client.get("/words/9999/edit", follow_redirects=True)
    assert "找不到编号为 9999 的词条" in response.text


# ---------------------------------------------------------------------------
# 增删改
# ---------------------------------------------------------------------------
def test_create_word_composes_tibetan_server_side(client):
    # 故意提交一个错误的 tibetan，服务端应当以组件合成的结果为准
    client.post("/words", data=form_payload(tibetan="这是错的"))

    word = models.list_words()[0]
    assert word.tibetan == "བོད"
    assert word.meaning == "西藏"
    assert word.tags == ["名词", "地名"]
    assert word.word_components.syllables[0].root == "བ"


def test_create_rejects_empty_components(client):
    response = client.post("/words", data={"components_json": "", "tibetan": "", "meaning": "空"})
    assert response.status_code == 400


def test_create_warns_but_still_saves(client):
    """不常见的组合只给提示，不阻止保存。"""
    response = client.post(
        "/words",
        data=form_payload(components_json=components_json({"prefix": "ག", "root": "མ"})),
        follow_redirects=False,
    )
    # 有提示时回到编辑页，让用户看到具体提示
    assert response.status_code == 303
    assert "/edit" in response.headers["location"]
    assert "level=warn" in response.headers["location"]
    assert models.count_words() == 1


def test_update_word(client):
    client.post("/words", data=form_payload())
    word = models.list_words()[0]

    response = client.post(
        f"/words/{word.id}",
        data=form_payload(
            components_json=components_json({"root": "ཆ", "vowel": "ུ"}),
            meaning="水",
            tags="名词",
        ),
        follow_redirects=False,
    )
    assert response.status_code == 303

    updated = models.get_word(word.id)
    assert updated.tibetan == "ཆུ"
    assert updated.meaning == "水"
    assert updated.tags == ["名词"]


def test_delete_word(client):
    client.post("/words", data=form_payload())
    word = models.list_words()[0]

    client.post(f"/words/{word.id}/delete")
    assert models.get_word(word.id) is None
    assert models.count_words() == 0


def test_delete_works_when_tibetan_contains_quotes(client):
    """藏文里有引号时删除按钮也必须能用。

    回归测试：早先「删除」的二次确认写成内联
    ``onsubmit="return confirm('确定删除「{{ word.tibetan }}」吗？');"``，
    HTML 实体先于 JS 解析，字段里的单引号会把 JS 字符串提前闭合，
    整个 onsubmit 变成语法错误 —— 按钮点了没反应，而且不会有任何报错。
    """
    models.import_payload([{"tibetan": "a'b", "meaning": "带单引号的词条"}], mode="replace")
    word = models.list_words()[0]

    # 词名走 data-* 属性，经 HTML 转义后仍能被 dataset 正确读回
    assert 'data-confirm="确定删除「a&#39;b」吗？"' in client.get("/").text

    client.post(f"/words/{word.id}/delete")
    assert models.count_words() == 0


#: 页面上唯一允许出现的内联事件处理器：提示条的关闭按钮，内容完全写死，不含用户数据。
SAFE_INLINE_HANDLERS = {"this.parentElement.remove()"}


def test_no_user_data_is_ever_pasted_into_inline_handlers(client):
    """护栏测试：用户数据不能出现在 ``on*="..."`` 内联事件处理器里。

    内联处理器里的内容是**先做 HTML 实体解码、再交给 JS 解析**的。
    所以 ``onclick="f('{{ 用户数据 }}')"`` 这种写法，只要数据里有一个引号，
    解码后就会把 JS 字符串提前闭合：轻则按钮静默失效（不报错，只是点了没反应），
    重则注入任意 JS。正确做法是放进 ``data-*`` 属性，用 JS 读取。

    这个断言不需要 JS 解析器：只要用户数据没被拼进内联处理器，
    页面上剩下的处理器就只能是那段写死的模板代码。
    """
    hostile = [
        {"tibetan": "a'b", "meaning": "单引号"},
        {"tibetan": 'a"b', "meaning": "双引号"},
        {"tibetan": "a<b>&c", "meaning": "尖括号与和号"},
        {"tibetan": "a\\b", "meaning": "反斜杠"},
        {"tibetan": "';alert(1);//", "meaning": "注入尝试"},
    ]
    models.import_payload(hostile, mode="replace")

    # 带上 msg 让顶部提示条渲染出来（它的关闭按钮是页面上唯一的静态内联处理器）
    page = client.get("/", params={"msg": "测试提示", "level": "warn"}).text
    # 只看 HTML 部分：<script> 里的内容不是 HTML 属性，不会成为内联处理器
    markup = re.sub(r"<script\b.*?</script>", "", page, flags=re.S)
    handlers = set(re.findall(r'\son[a-z]+\s*=\s*"([^"]*)"', markup))

    assert handlers, "页面上应当有提示条的静态关闭按钮，否则这个测试失去了意义"
    assert handlers <= SAFE_INLINE_HANDLERS, f"出现了含动态内容的内联处理器：{handlers}"

    # 顺带确认注入尝试确实被当成普通文本渲染在了表格里
    assert "&#39;;alert(1);//" in page


def test_clear_all_words(client):
    for meaning in ("甲", "乙", "丙"):
        client.post("/words", data=form_payload(meaning=meaning))
    assert models.count_words() == 3

    client.post("/words/clear")
    assert models.count_words() == 0


def test_multi_syllable_word_and_auto_join_toggle(client):
    two_syllables = components_json({"root": "ཉ", "vowel": "ི"}, {"root": "མ"})

    client.post("/words", data=form_payload(
        components_json=two_syllables, meaning="太阳", auto_join="on",
    ))
    assert models.list_words()[0].tibetan == "ཉི་མ"

    # 取消勾选「自动连接」时，浏览器的表单里根本不会带上 auto_join 这个键
    without_join = form_payload(components_json=two_syllables, meaning="太阳（不连接）")
    without_join.pop("auto_join")
    client.post("/words", data=without_join)

    words = {w.meaning: w.tibetan for w in models.list_words()}
    assert words["太阳"] == "ཉི་མ"
    assert words["太阳（不连接）"] == "ཉིམ"


# ---------------------------------------------------------------------------
# 预览 API
# ---------------------------------------------------------------------------
def test_api_preview(client):
    response = client.post("/api/preview", json={
        "auto_join": True,
        "syllables": [{
            "prefix": "བ", "superscript": "ས", "root": "ག", "subjoined": "ར",
            "vowel": "ུ", "suffix": "བ", "suffix2": "ས",
        }],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["tibetan"] == "བསྒྲུབས།"
    assert body["syllable_strings"] == ["བསྒྲུབས"]
    assert body["issues"] == []


def test_api_preview_reports_errors_and_warnings(client):
    response = client.post("/api/preview", json={
        "auto_join": True,
        "syllables": [{"vowel": "ི"}, {"prefix": "ག", "root": "མ"}],
    })
    issues = response.json()["issues"]
    levels = {issue["level"] for issue in issues}
    assert "error" in levels       # 第一个音节缺基字
    assert "warning" in levels     # 第二个音节组合不常见


def test_api_preview_rejects_bad_body(client):
    assert client.post("/api/preview", json=[1, 2, 3]).status_code == 400


@pytest.mark.parametrize("body", [
    '{"syllables": "x"}',
    '{"syllables": 5}',
    '{"syllables": [1, 2, 3]}',
    '{"syllables": {"a": 1}}',
    '{"syllables": null}',
    '{"syllables": [{"root": ["ཀ"]}]}',
    '{"syllables": [{"root": 123}]}',
    '{"syllables": [{"subjoined": {"x": 1}}]}',
])
def test_malformed_components_never_500(client, body):
    """畸形组件结构不能让接口 500 —— 前端实时预览一直在调这个接口。"""
    assert client.post(
        "/api/preview", content=body, headers={"Content-Type": "application/json"}
    ).status_code == 200

    # 表单路径同理
    assert client.post(
        "/words", data={"components_json": body, "tibetan": "བོད", "meaning": "x"}
    ).status_code in (200, 400)


@pytest.mark.parametrize("url", [
    "/words/10000000000000000000000000/edit",
    "/export?scope=selected&ids=10000000000000000000000000",
    "/export?scope=selected&ids=%C2%B2",       # '²'.isdigit() 为真，但 int('²') 会抛异常
    "/export?scope=selected&ids=,,,",
    "/export?scope=selected&ids=",
])
def test_out_of_range_or_junk_ids_never_500(client, url):
    assert client.get(url).status_code == 200


def test_duplicate_ids_in_export_are_deduped(client):
    """URL 里写 ids=1,1,1 不能让同一个词导出三遍。"""
    client.post("/words", data=form_payload())
    word = models.list_words()[0]

    response = client.get(f"/export?scope=selected&ids={word.id},{word.id},{word.id}")
    assert "共 <strong>1</strong> 条" in response.text

    data = client.post("/export/docx", data={
        "scope": "selected", "ids": f"{word.id},{word.id}", "order": "sequential",
        # 显式用表格排版，这样能从表格行数直接数出词条个数
        "layout": "table", "landscape": "on",
        "title": "", "include_meaning": "on", "q": "", "tag": "", "seed": "",
    })
    document = Document(io.BytesIO(data.content))
    assert len(document.tables[0].rows) == 1


# ---------------------------------------------------------------------------
# Word 导出
# ---------------------------------------------------------------------------
def test_export_page_shows_scope_and_preview(client):
    client.post("/words", data=form_payload())
    response = client.get("/export?scope=all")

    assert response.status_code == 200
    assert "全部词条" in response.text
    assert "顺序导出" in response.text and "乱序导出" in response.text
    assert "包含释义" in response.text and "隐藏释义" in response.text
    assert "Microsoft Himalaya" in response.text


def test_export_docx_downloads_sequential(client):
    client.post("/words", data=form_payload())
    response = client.post("/export/docx", data={
        "scope": "all", "order": "sequential", "title": "藏语基础词汇",
        "include_meaning": "on", "seed": "", "q": "", "tag": "", "ids": "",
    })

    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX_MIME

    disposition = unquote(response.headers["content-disposition"])
    assert "顺序" in disposition
    assert ".docx" in disposition

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "བོད" in document_xml
    assert "藏语基础词汇" in document_xml
    assert 'w:cs="Microsoft Himalaya"' in document_xml
    assert "w:szCs" in document_xml


def test_create_word_with_trailing_shad_toggle(client):
    """「词尾加 །」通过表单控制：勾了加、不勾不加。"""
    client.post("/words", data=form_payload(meaning="带ཤད", trailing_shad="on"))
    client.post("/words", data=form_payload(meaning="不带ཤད"))   # 不提交该字段 = 取消勾选

    words = {w.meaning: w.tibetan for w in models.list_words()}
    assert words["带ཤད"] == "བོད།"
    assert words["不带ཤད"] == "བོད"


def test_edit_page_has_trailing_shad_checkbox(client):
    """复选框要按词条自己的设置回填。

    注意 form_payload() 里不带 trailing_shad 字段——HTML 复选框不勾选时
    压根不会提交，所以「缺席」就代表关闭，这跟浏览器行为一致。
    """
    client.post("/words", data=form_payload(meaning="不带", trailing_shad="on"))
    client.post("/words", data=form_payload(meaning="不带就缺席"))

    words = {w.meaning: w for w in models.list_words()}
    on_page = client.get(f"/words/{words['不带'].id}/edit").text
    off_page = client.get(f"/words/{words['不带就缺席'].id}/edit").text

    assert 'id="trailing_shad"' in on_page
    assert 'id="trailing_shad" name="trailing_shad" checked' in on_page
    assert '"trailing_shad": true' in on_page

    assert 'id="trailing_shad"' in off_page
    assert 'id="trailing_shad" name="trailing_shad" checked' not in off_page
    assert '"trailing_shad": false' in off_page


def test_direct_typed_tibetan_gets_shad_only_once(client):
    """没有组件的词条走「直接输入藏文」那条路时，反复保存不会越加越多。"""
    models.import_payload([{"tibetan": "བོད", "meaning": "西藏"}], mode="replace")
    word = models.list_words()[0]

    payload = {
        "components_json": '{"version": 2, "auto_join": true, "trailing_shad": true, "syllables": []}',
        "meaning": "西藏", "note": "", "tags": "", "trailing_shad": "on",
    }
    # 第一次：用户框里没有 །，保存后应该补上
    client.post(f"/words/{word.id}", data={**payload, "tibetan": "བོད"})
    assert models.get_word(word.id).tibetan == "བོད།"

    # 第二次：框里已经带着 ། 了，保存后仍然只有一个
    client.post(f"/words/{word.id}", data={**payload, "tibetan": "བོད།"})
    assert models.get_word(word.id).tibetan == "བོད།"

    # 取消勾选则原样保留用户输入
    client.post(f"/words/{word.id}", data={**payload, "tibetan": "བོད།"})
    assert models.get_word(word.id).tibetan == "བོད།"


def test_export_layout_choice_reaches_the_document(client):
    """导出设置页的「排版方式」要真的改变生成的文档。"""
    client.post("/words", data=form_payload())

    base = {
        "scope": "all", "order": "sequential", "title": "", "include_meaning": "on",
        "q": "", "tag": "", "ids": "", "seed": "", "landscape": "on",
    }

    columns = client.post("/export/docx", data={**base, "layout": "columns",
                                                "column_count": "3"})
    doc_columns = Document(io.BytesIO(columns.content))
    assert doc_columns.tables == []
    assert 'w:num="3"' in zipfile.ZipFile(io.BytesIO(columns.content)).read(
        "word/document.xml").decode()

    table = client.post("/export/docx", data={**base, "layout": "table"})
    doc_table = Document(io.BytesIO(table.content))
    assert len(doc_table.tables) == 1
    assert len(doc_table.tables[0].rows) == 1


@pytest.mark.parametrize("bad", ["", "5", "abc", "-1", "²"])
def test_export_falls_back_on_invalid_column_count(client, bad):
    """栏数被手改成非法值时回落到默认的 3 栏，而不是报错。"""
    client.post("/words", data=form_payload())
    response = client.post("/export/docx", data={
        "scope": "all", "order": "sequential", "title": "", "include_meaning": "on",
        "q": "", "tag": "", "ids": "", "seed": "", "layout": "columns",
        "column_count": bad, "landscape": "on",
    })
    assert response.status_code == 200
    xml = zipfile.ZipFile(io.BytesIO(response.content)).read("word/document.xml").decode()
    assert 'w:num="3"' in xml


def tibetan_szcs(response) -> str:
    """取出导出文档里第一个藏文 run 的字号（w:szCs，单位半磅）。"""
    from docx.oxml.ns import qn

    document = Document(io.BytesIO(response.content))
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        if any(0x0F00 <= ord(c) <= 0x0FFF for c in run.text):
                            return run._element.rPr.find(qn("w:szCs")).get(qn("w:val"))
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            if any(0x0F00 <= ord(c) <= 0x0FFF for c in run.text):
                return run._element.rPr.find(qn("w:szCs")).get(qn("w:val"))
    raise AssertionError("文档里没有藏文 run")


def test_export_page_has_tibetan_size_selector(client):
    response = client.get("/export?scope=all")
    assert 'name="tibetan_size"' in response.text
    assert "藏文字号" in response.text


def test_export_tibetan_size_reaches_the_document(client):
    client.post("/words", data=form_payload())
    base = {
        "scope": "all", "order": "sequential", "title": "", "include_meaning": "on",
        "q": "", "tag": "", "ids": "", "seed": "", "layout": "columns",
        "column_count": "3", "landscape": "on",
    }
    # 默认 20 磅 -> w:szCs = 40
    assert tibetan_szcs(client.post("/export/docx", data=base)) == "40"
    # 显式选 28 磅 -> 56
    assert tibetan_szcs(client.post("/export/docx", data={**base, "tibetan_size": "28"})) == "56"


@pytest.mark.parametrize("bad", ["", "abc", "999", "-3", "20.5"])
def test_export_falls_back_on_invalid_tibetan_size(client, bad):
    """字号被改成非法值时回落到默认的 20 磅，而不是报错。"""
    client.post("/words", data=form_payload())
    response = client.post("/export/docx", data={
        "scope": "all", "order": "sequential", "title": "", "include_meaning": "on",
        "q": "", "tag": "", "ids": "", "seed": "", "layout": "columns",
        "column_count": "3", "landscape": "on", "tibetan_size": bad,
    })
    assert response.status_code == 200
    assert tibetan_szcs(response) == "40"


def test_export_falls_back_on_unknown_layout(client):
    client.post("/words", data=form_payload())
    response = client.post("/export/docx", data={
        "scope": "all", "order": "sequential", "title": "", "include_meaning": "on",
        "q": "", "tag": "", "ids": "", "seed": "", "layout": "乱写的",
        "column_count": "3", "landscape": "on",
    })
    assert response.status_code == 200
    assert Document(io.BytesIO(response.content)).tables == []   # 回落到分栏


def test_export_docx_random_filename(client):
    client.post("/words", data=form_payload())
    response = client.post("/export/docx", data={
        "scope": "all", "order": "random", "title": "", "seed": "7",
        "include_meaning": "on", "q": "", "tag": "", "ids": "",
    })
    assert "乱序" in unquote(response.headers["content-disposition"])


def test_export_docx_hidden_meaning_blanks_the_second_column(client):
    client.post("/words", data=form_payload())
    response = client.post("/export/docx", data={
        "scope": "all", "order": "sequential", "title": "",
        "include_meaning": "on", "hide_meaning": "on",
        "seed": "", "q": "", "tag": "", "ids": "",
    })

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "བོད" in document_xml
    assert "西藏" not in document_xml      # 释义被隐藏


def test_export_docx_by_selected_ids(client):
    client.post("/words", data=form_payload())
    client.post("/words", data=form_payload(
        components_json=components_json({"root": "ཆ", "vowel": "ུ"}),
        meaning="水", tags="名词",
    ))
    water = [w for w in models.list_words() if w.meaning == "水"][0]

    response = client.post("/export/docx", data={
        "scope": "selected", "ids": str(water.id), "order": "sequential",
        "title": "", "include_meaning": "on", "q": "", "tag": "", "seed": "",
    })
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "ཆུ" in document_xml
    assert "བོད" not in document_xml      # 没勾选的词条不应出现


def test_export_docx_with_no_words_still_returns_a_file(client):
    response = client.post("/export/docx", data={
        "scope": "all", "order": "sequential", "title": "",
        "include_meaning": "on", "q": "", "tag": "", "ids": "", "seed": "",
    })
    assert response.status_code == 200
    assert len(response.content) > 0


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------
def test_backup_export_downloads_json(client):
    client.post("/words", data=form_payload())
    response = client.get("/backup/export")

    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    payload = json.loads(response.text)
    assert payload["format"] == "zwjy-tibetan-vocabulary"
    assert payload["count"] == 1
    assert payload["words"][0]["tibetan"] == "བོད"


def test_backup_import_append_and_replace(client, sample_words):
    response = client.post(
        "/backup/import",
        files={"file": ("sample.json", json.dumps({"words": sample_words[:5]},
                                                  ensure_ascii=False).encode("utf-8"),
                        "application/json")},
        data={"mode": "append"},
    )
    assert response.status_code == 200
    assert models.count_words() == 5

    # 再导一次，追加模式会跳过重复
    client.post(
        "/backup/import",
        files={"file": ("sample.json", json.dumps({"words": sample_words[:5]},
                                                  ensure_ascii=False).encode("utf-8"),
                        "application/json")},
        data={"mode": "append"},
    )
    assert models.count_words() == 5

    # 覆盖模式换成完整示例数据
    client.post(
        "/backup/import",
        files={"file": ("sample.json", json.dumps({"words": sample_words},
                                                  ensure_ascii=False).encode("utf-8"),
                        "application/json")},
        data={"mode": "replace"},
    )
    assert models.count_words() == len(sample_words)


def test_backup_import_rejects_broken_file(client):
    response = client.post(
        "/backup/import",
        files={"file": ("bad.json", b"{ this is not json", "application/json")},
        data={"mode": "append"},
    )
    assert response.status_code == 200
    assert "导入失败" in response.text
    assert models.count_words() == 0


def test_backup_import_failure_keeps_library_intact(client, monkeypatch):
    """导入中途出错时，界面上要看到可读提示，词库一条都不能少。"""
    client.post("/words", data=form_payload())
    client.post("/words", data=form_payload(meaning="水"))
    before = models.count_words()

    def boom(*args, **kwargs):
        raise RuntimeError("模拟写入失败")

    monkeypatch.setattr(models, "_word_from_backup", boom)
    response = client.post(
        "/backup/import",
        files={"file": ("x.json", b'{"words":[{"tibetan":"\\u0f56\\u0f7c\\u0f51"}]}',
                        "application/json")},
        data={"mode": "replace"},
    )

    assert response.status_code == 200            # 不是 500
    assert "导入失败" in response.text
    assert "词库未做任何改动" in response.text
    assert models.count_words() == before         # 没有被清空


# ---------------------------------------------------------------------------
# 静态资源
# ---------------------------------------------------------------------------
def test_static_assets_are_served(client):
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
