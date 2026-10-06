# -*- coding: utf-8 -*-
"""
app.py —— 藏语词汇录入与 Word 导出工具（FastAPI 应用入口）
==========================================================

启动方式::

    python app.py                 # 默认 http://127.0.0.1:8000
    python app.py --port 9000     # 换端口
    python app.py --host 0.0.0.0  # 允许局域网访问
    python app.py --reload        # 开发模式，改代码自动重启

路由一览
--------
======================  ====================================================
GET  /                   词条列表（搜索、标签筛选、排序、勾选）
GET  /words/new          新增词条
POST /words              提交新增
GET  /words/{id}/edit    编辑词条（回填音节卡片）
POST /words/{id}         提交修改
POST /words/{id}/delete  删除词条
POST /words/clear        清空全部词条
POST /api/preview        组件 -> 合成藏文 + 校验（前端实时预览用）
GET  /export             导出设置页
POST /export/docx        生成并下载 Word
GET  /backup/export      下载 JSON 备份
POST /backup/import      上传 JSON 导入
======================  ====================================================
"""

from __future__ import annotations

import argparse
import json
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import models
from export_docx import (
    COLUMN_CHOICES,
    DEFAULT_COLUMN_COUNT,
    DEFAULT_TIBETAN_SIZE,
    LAYOUT_COLUMNS,
    LAYOUT_LABELS,
    LAYOUT_TABLE,
    ORDER_RANDOM,
    ORDER_SEQUENTIAL,
    TIBETAN_SIZE_CHOICES,
    TIBETAN_SIZE_LABELS,
    ExportOptions,
    apply_order,
    build_filename,
    export_to_bytes,
    font_choices,
)
from tibetan import (
    COMPONENT_LABELS,
    MAX_SUBJOINED,
    SHAD,
    WordComponents,
    build_component_options,
    compose_word,
    normalize_text,
    preview,
    validate_word,
)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时确保数据表存在。"""
    models.init_db()
    yield


app = FastAPI(
    title="藏语词汇录入与导出工具",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 建表是幂等的，这里在导入时就做一次，避免用非标准方式拉起应用时
# （例如直接用 TestClient、或某些 ASGI 宿主）出现「no such table」的困惑。
models.init_db()


def render(request: Request, template: str, **context: Any) -> HTMLResponse:
    """统一的模板渲染入口，自动带上全部页面都要用的公共变量。

    这里只放**每页都用得到**的东西（页脚的词条总数）。像标签列表这种只有
    首页需要的，由各页面自己传——否则新增页、编辑页、导出页每渲染一次都要
    白白全表扫一遍标签。
    """
    context.setdefault("request", request)
    context.setdefault("component_labels", COMPONENT_LABELS)
    context.setdefault("total_count", models.count_words())
    # 编辑页的 JS 用它来决定下加字最多能叠几层
    context.setdefault("max_subjoined", MAX_SUBJOINED)
    return templates.TemplateResponse(request, template, context)


def redirect(url: str, msg: str = "", level: str = "ok") -> RedirectResponse:
    """带提示信息跳转（用查询参数传提示，避免引入 session）。"""
    if msg:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}msg={quote(msg)}&level={level}"
    # 303 让浏览器把 POST 转成 GET，避免刷新时重复提交
    return RedirectResponse(url=url, status_code=303)


# ---------------------------------------------------------------------------
# 表单解析
# ---------------------------------------------------------------------------
async def word_from_request(request: Request) -> tuple[models.Word, list]:
    """从提交的表单里解析出一个词条。

    藏文以**服务端重新合成**的结果为准，不信任前端传来的字符串，
    这样「合成藏文」和「组件 JSON」永远是一致的。
    """
    form = await request.form()

    raw_components = form.get("components_json") or ""
    try:
        payload = json.loads(raw_components)
    except (TypeError, ValueError):
        payload = None

    components = WordComponents.from_dict(payload if isinstance(payload, dict) else None)
    components.auto_join = bool(form.get("auto_join"))
    components.trailing_shad = bool(form.get("trailing_shad"))

    issues = validate_word(components)
    composed = compose_word(components).strip()

    if composed:
        tibetan = composed
    else:
        # 组件为空（例如用户自己粘贴了一串现成藏文）时，退回使用提交的藏文文本。
        # 这段文本服务端没法重新合成，所以「词尾加 །」要在这里手动应用一次：
        # 先去掉结尾已有的 ། 再补一个，保证反复保存不会越加越多。
        typed = normalize_text(str(form.get("tibetan") or "").strip())
        if typed and components.trailing_shad:
            typed = typed.rstrip(SHAD) + SHAD
        tibetan = typed

    if not tibetan:
        raise HTTPException(status_code=400, detail="藏文词语不能为空，请至少填写一个音节的基字。")

    word = models.Word(
        tibetan=tibetan,
        components=components.to_json(),
        meaning=str(form.get("meaning") or "").strip(),
        note=str(form.get("note") or "").strip(),
        tags=models.normalize_tags(str(form.get("tags") or "")),
        auto_join=components.auto_join,
    )
    return word, issues


# ---------------------------------------------------------------------------
# 列表 / 增删改
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = "",
    tag: str = "",
    sort: str = models.DEFAULT_SORT,
):
    """首页：词条列表 + 搜索 + 标签筛选 + 排序。"""
    words = models.list_words(q=q, tag=tag, sort=sort)
    return render(
        request,
        "index.html",
        words=words,
        q=q,
        tag=tag,
        sort=sort,
        sort_options=models.SORT_OPTIONS,
        all_tags=models.all_tags(),   # 只有首页的标签下拉框需要
        msg=request.query_params.get("msg", ""),
        level=request.query_params.get("level", "ok"),
    )


@app.get("/words/new", response_class=HTMLResponse)
def new_word(request: Request):
    """新增词条页。默认给一张空音节卡片。"""
    empty = WordComponents(syllables=[], auto_join=True)
    return render(
        request,
        "edit.html",
        mode="new",
        word=None,
        initial=empty.to_dict(),
        options=build_component_options(),
        saved=False,
    )


@app.post("/words")
async def create_word(request: Request):
    word, issues = await word_from_request(request)
    word_id = models.create_word(word)

    if issues:
        # 有组合提示时，把用户带回编辑页（那里会展示完整提示），而不是直接回列表
        return redirect(f"/words/{word_id}/edit", msg=f"已保存「{word.tibetan}」", level="warn")
    return redirect("/", msg=f"已新增「{word.tibetan}」")


@app.post("/words/clear")
def clear_words():
    """清空全部词条。

    注意：这条路由必须注册在 ``POST /words/{word_id}`` **之前**。
    Starlette 按注册顺序匹配，而 ``{word_id}`` 的路径正则会先吃掉 "clear"，
    之后 FastAPI 的 int 校验会把它变成 422，导致这里永远命中不了。
    """
    removed = models.delete_all_words()
    return redirect("/", msg=f"已清空全部词条（共 {removed} 条）。", level="warn")


@app.get("/words/{word_id}/edit", response_class=HTMLResponse)
def edit_word(request: Request, word_id: int):
    word = models.get_word(word_id)
    if word is None:
        return redirect("/", msg=f"找不到编号为 {word_id} 的词条。", level="warn")

    components = word.word_components
    initial = components.to_dict()
    if not components.meaningful_syllables():
        # 老数据 / 手工导入的数据可能没有组件，给一张空卡片以便重新录入。
        # 「词尾加 །」按它现有的藏文推断，免得勾选框显示的跟实际写法对不上。
        initial = WordComponents(
            syllables=[],
            auto_join=word.auto_join,
            trailing_shad=word.tibetan.endswith(SHAD),
        ).to_dict()

    return render(
        request,
        "edit.html",
        mode="edit",
        word=word,
        initial=initial,
        options=build_component_options(),
        saved=bool(request.query_params.get("msg")),
        msg=request.query_params.get("msg", ""),
        level=request.query_params.get("level", "ok"),
    )


@app.post("/words/{word_id}")
async def update_word(request: Request, word_id: int):
    if models.get_word(word_id) is None:
        return redirect("/", msg=f"找不到编号为 {word_id} 的词条。", level="warn")

    word, issues = await word_from_request(request)
    models.update_word(word_id, word)

    if issues:
        return redirect(f"/words/{word_id}/edit", msg=f"已保存「{word.tibetan}」", level="warn")
    return redirect("/", msg=f"已更新「{word.tibetan}」")


@app.post("/words/{word_id}/delete")
def delete_word(word_id: int):
    word = models.get_word(word_id)
    if word is None:
        return redirect("/", msg=f"找不到编号为 {word_id} 的词条。", level="warn")
    models.delete_word(word_id)
    return redirect("/", msg=f"已删除「{word.tibetan}」")


# ---------------------------------------------------------------------------
# 实时预览 API
# ---------------------------------------------------------------------------
@app.post("/api/preview")
async def api_preview(request: Request) -> JSONResponse:
    """接收前端当前的音节结构，返回合成结果与校验提示。

    这个接口不做任何持久化，纯计算，供编辑页实时预览使用。
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是合法的 JSON。"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"error": "请求体必须是对象。"}, status_code=400)

    # 合成与校验的逻辑都在 tibetan.preview() 里，这里不重复实现一遍
    return JSONResponse(preview(payload))


# ---------------------------------------------------------------------------
# Word 导出
# ---------------------------------------------------------------------------
def _collect_export_rows(
    scope: str,
    q: str,
    tag: str,
    ids: str,
) -> list[models.Word]:
    """按导出范围取出词条，并统一按「录入顺序」排好。

    顺序导出时保持这个顺序；乱序导出时再由 :func:`apply_order` 打乱。
    """
    if scope == "selected":
        # parse_ids 会忽略非法项、去重，并挡掉超出 SQLite 整数范围的值，
        # 免得用户手改 URL 里的 ids 就能把接口打成 500
        rows = models.get_words_by_ids(models.parse_ids(ids))
        # 手动勾选也统一回归录入顺序，保证两遍导出结果可比
        rows.sort(key=lambda w: (w.created_at, w.id or 0))
        return rows

    if scope == "filtered":
        return models.list_words(q=q, tag=tag, sort="created_asc")

    return models.list_words(sort="created_asc")


@app.get("/export", response_class=HTMLResponse)
def export_page(
    request: Request,
    scope: str = "all",
    q: str = "",
    tag: str = "",
    ids: str = "",
):
    """导出设置页。

    这里刻意不接受 ``sort`` 参数：导出顺序只由「顺序 / 乱序」决定，
    顺序导出固定按**录入先后**（创建时间升序），
    与列表页当前选的排序方式无关——否则「顺序导出」的含义会随列表排序漂移。
    """
    rows = _collect_export_rows(scope, q, tag, ids)
    scope_names = {
        "all": "全部词条",
        "filtered": "当前搜索结果",
        "selected": "手动勾选的词条",
    }
    return render(
        request,
        "export.html",
        scope=scope,
        scope_name=scope_names.get(scope, "全部词条"),
        q=q,
        tag=tag,
        ids=ids,
        row_count=len(rows),
        preview_rows=rows[:5],
        fonts=font_choices(),
        layouts=LAYOUT_LABELS,
        layout_choices=(LAYOUT_COLUMNS, LAYOUT_TABLE),
        column_choices=COLUMN_CHOICES,
        default_column_count=DEFAULT_COLUMN_COUNT,
        tibetan_sizes=TIBETAN_SIZE_CHOICES,
        tibetan_size_labels=TIBETAN_SIZE_LABELS,
        default_tibetan_size=DEFAULT_TIBETAN_SIZE,
        default_title=f"藏语词汇 {date.today().strftime('%Y-%m-%d')}",
    )


def parse_column_count(raw: str) -> int:
    """把表单里的栏数收敛到允许的取值，非法值回落到默认栏数。"""
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_COLUMN_COUNT
    return count if count in COLUMN_CHOICES else DEFAULT_COLUMN_COUNT


def parse_tibetan_size(raw: str) -> float:
    """把表单里的藏文字号收敛到允许的取值，非法值回落到默认字号。"""
    try:
        size = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIBETAN_SIZE
    return size if size in TIBETAN_SIZE_CHOICES else DEFAULT_TIBETAN_SIZE


@app.post("/export/docx")
async def export_docx(request: Request):
    """生成 Word 并作为附件下载。"""
    form = await request.form()

    def field(name: str, default: str = "") -> str:
        return str(form.get(name) or default)

    layout = field("layout", LAYOUT_COLUMNS)
    if layout not in (LAYOUT_COLUMNS, LAYOUT_TABLE):
        layout = LAYOUT_COLUMNS

    options = ExportOptions(
        title=field("title"),
        order=ORDER_RANDOM if field("order", ORDER_SEQUENTIAL) == ORDER_RANDOM else ORDER_SEQUENTIAL,
        include_meaning=bool(form.get("include_meaning")),
        hide_meaning=bool(form.get("hide_meaning")),
        seed=field("seed"),
        tibetan_font=field("tibetan_font", font_choices()[0]),
        tibetan_size_pt=parse_tibetan_size(field("tibetan_size")),
        layout=layout,
        column_count=parse_column_count(field("column_count")),
        landscape=bool(form.get("landscape")),
    )

    rows = _collect_export_rows(
        scope=field("scope", "all"),
        q=field("q"),
        tag=field("tag"),
        ids=field("ids"),
    )
    rows = apply_order(rows, options)

    data = export_to_bytes(rows, options)
    filename = build_filename(options)
    ascii_fallback = "vocabulary_random.docx" if options.order == ORDER_RANDOM else "vocabulary_sequential.docx"

    headers = {
        # 同时给出 ASCII 回退名和 RFC 5987 的 UTF-8 名，兼容各种浏览器
        "Content-Disposition": (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }
    # 用 Response 而不是 StreamingResponse：内容是已经生成好的整块 bytes，
    # 这样 Starlette 会正确带上 Content-Length，浏览器才能显示下载进度。
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# 备份：导出 / 导入 JSON
# ---------------------------------------------------------------------------
@app.get("/backup/export")
def backup_export():
    """下载整个词库的 JSON 备份。"""
    text = models.export_json_text()
    filename = f"藏语词库备份_{date.today().strftime('%Y%m%d')}.json"
    headers = {
        "Content-Disposition": (
            'attachment; filename="vocabulary_backup.json"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }
    return PlainTextResponse(text, media_type="application/json", headers=headers)


@app.post("/backup/import")
async def backup_import(request: Request, file: UploadFile = File(...)):
    """导入 JSON 备份。"""
    form = await request.form()
    mode = str(form.get("mode") or "append")
    if mode not in ("append", "replace"):
        mode = "append"

    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        return redirect("/", msg=f"导入失败：文件不是合法的 UTF-8 JSON（{exc}）。", level="warn")

    try:
        stats = models.import_payload(payload, mode=mode)
    except ValueError as exc:
        # 解析层主动抛出的可读错误（例如「第 N 条记录无法解析」）
        return redirect("/", msg=f"导入失败：{exc}", level="warn")
    except Exception as exc:
        # 兜底：导入已经做成"要么全成、要么全不动"，所以这里不会留下半截数据。
        # 但仍然要把异常转成一句人话，而不是丢一个 FastAPI 的 500 页面给用户。
        return redirect(
            "/",
            msg=f"导入失败（{type(exc).__name__}）：{exc}。词库未做任何改动。",
            level="warn",
        )

    action = "覆盖" if mode == "replace" else "追加"
    message = f"导入完成（{action}）：新增 {stats['added']} 条"
    if stats["removed"]:
        message += f"，先清空了 {stats['removed']} 条旧数据"
    if stats["skipped"]:
        message += f"，跳过 {stats['skipped']} 条（重复或缺少藏文）"
    return redirect("/", msg=message + "。")


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="藏语词汇录入与 Word 导出工具",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1（仅本机）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码改动自动重启")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    args = parse_args(argv)
    models.init_db()

    print("=" * 56)
    print("  藏语词汇录入与 Word 导出工具")
    print(f"  数据库：{models.db_path()}")
    print(f"  访问地址：http://{args.host}:{args.port}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)

    uvicorn.run(
        "app:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
