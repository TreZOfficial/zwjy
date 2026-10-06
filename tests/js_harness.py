# -*- coding: utf-8 -*-
"""
js_harness.py —— 把 memorize/index.html 里的 JS 跑起来的测试脚手架
==================================================================

网页里的合成器是 ``tibetan.py`` 的一份 JS 镜像。两份实现分开写，就必然会有
走偏的风险（改了一边忘了另一边）。这个脚手架用 dukpy（一个嵌入式 JS 引擎）
把 HTML 里**真实的**那段脚本加载起来执行，让测试可以直接拿它的输出跟 Python
比对。

页面脚本一上来就会操作 DOM，所以这里先塞一个够用的 DOM 替身进去。
替身只实现页面真正用到的那几个方法，不追求完整。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HTML_PATH = BASE_DIR / "memorize" / "index.html"

#: 取内嵌词库
_BANK = re.compile(
    r'<script type="application/json" id="wordbank"([^>]*)>(.*?)</script>', re.DOTALL
)
#: 取应用脚本。**先把词库那段整块删掉再找**，不能只靠负向断言：
#: 文件里任何一处出现 script 标签的字面文本（注释、说明）都会把人骗过去，
#: 早先就踩过——注释里写了示例标签，结果把整段词库当成了脚本。
_APP_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)

#: 页面脚本用到的 DOM 接口的最小替身
DOM_SHIM = r"""
var __els = {};
function __el(id) {
  if (__els[id]) return __els[id];
  var e = {
    id: id, dataset: {}, style: {}, _kids: [],
    textContent: '', innerHTML: '', className: '',
    checked: false, disabled: false,
    classList: {
      toggle: function () {}, add: function () {}, remove: function () {},
      contains: function () { return false; }
    },
    addEventListener: function () {},
    appendChild: function (c) { this._kids.push(c); return c; },
    insertAdjacentHTML: function () {},
    querySelectorAll: function () { return []; },
    querySelector: function () { return null; }
  };
  __els[id] = e;
  return e;
}
var document = {
  getElementById: __el,
  querySelectorAll: function () { return []; },
  createElement: function (tag) { return __el('new:' + tag + ':' + Math.random()); },
  createTextNode: function (text) { return { nodeValue: text, textContent: text }; },
  addEventListener: function () {},
  body: __el('body')
};
var window = {
  localStorage: undefined,
  scrollTo: function () {},
  confirm: function () { return false; }
};
"""


def read_html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def strip_bank_block(html: str) -> str:
    """把内嵌词库那一段整块删掉，剩下的才用来找应用脚本。"""
    return _BANK.sub("<script type=\"application/json\" id=\"wordbank\"></script>", html)


def extract_app_script(html: str | None = None) -> str:
    """取出页面里的应用脚本（不是内嵌词库那段 JSON）。"""
    html = html if html is not None else read_html()
    scripts = _APP_SCRIPT.findall(strip_bank_block(html))
    if not scripts:
        raise AssertionError("index.html 里没找到应用脚本")
    return max(scripts, key=len)


def extract_bank_text(html: str | None = None) -> str:
    """取出内嵌词库那段 JSON 的原始文本。"""
    html = html if html is not None else read_html()
    match = _BANK.search(html)
    if not match:
        raise AssertionError("index.html 里没找到 id=wordbank 的 JSON")
    return match.group(2)


def extract_bank(html: str | None = None) -> list[dict]:
    """把内嵌词库解析成 Python 对象。

    页面在生成时会把 ``</`` 转义成 ``<\\/``（防止提前闭合 script 标签），
    这里再还原回去。
    """
    return json.loads(extract_bank_text(html).replace("<\\/", "</"))


def load_app():
    """把页面脚本跑起来，返回一个可以继续 evaljs 的解释器。

    用法::

        interp = load_app()
        interp.evaljs('composeWord([SYL], true, true)')
    """
    import dukpy

    html = read_html()
    bank_text = extract_bank_text(html)
    attributes = _BANK.search(html).group(1)
    generated_at = re.search(r'data-generated-at="([^"]*)"', attributes)

    interp = dukpy.JSInterpreter()
    interp.evaljs(DOM_SHIM)
    # 页面一启动就要读词库，先把替身元素喂饱
    interp.evaljs(
        "document.getElementById('wordbank').textContent = " + json.dumps(bank_text) + ";"
    )
    interp.evaljs(
        "document.getElementById('wordbank').dataset.generatedAt = "
        + json.dumps(generated_at.group(1) if generated_at else "")
        + ";"
    )
    interp.evaljs(extract_app_script(html))
    return interp


def call(interp, func: str, *args) -> object:
    """调用页面钩子 ``window.__zwjyMemory`` 上的一个函数。

    页面代码整个包在一个立即执行函数里（这是对的，不污染全局），
    所以只能通过那个显式暴露的钩子去调。

    参数里凡是形如 ``("__json__", 值)`` 的，会用 JSON.parse 还原成对象，
    避免把 Python 的 dict 直接拼进 JS 源码。
    """
    pieces = []
    for index, arg in enumerate(args):
        if isinstance(arg, tuple) and len(arg) == 2 and arg[0] == "__json__":
            name = f"__arg{index}"
            interp.evaljs(
                f"var {name} = JSON.parse({json.dumps(json.dumps(arg[1], ensure_ascii=False))});"
            )
            pieces.append(name)
        else:
            pieces.append(json.dumps(arg, ensure_ascii=False))
    return interp.evaljs(f"window.__zwjyMemory.{func}({', '.join(pieces)})")
