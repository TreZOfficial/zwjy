# -*- coding: utf-8 -*-
"""pytest 公共夹具。

关键点：**在导入任何业务模块之前**把 ``ZWJY_DB`` 指向临时文件，
这样测试永远不会碰到 ``data/words.db`` 里的真实数据。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在 import models / app 之前设置
_TMP_DIR = Path(tempfile.mkdtemp(prefix="zwjy-tests-"))
os.environ["ZWJY_DB"] = str(_TMP_DIR / "test.db")

import pytest  # noqa: E402

import models  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """测试跑完后清掉临时数据库目录。"""
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试用例开始前建表并清空，保证用例之间互不影响。"""
    models.init_db()
    models.delete_all_words()
    yield
    models.delete_all_words()


@pytest.fixture
def sample_words() -> list[dict]:
    """读取项目自带的示例数据。"""
    import json

    path = ROOT / "data" / "sample_words.json"
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["words"]


@pytest.fixture
def client():
    """FastAPI 测试客户端（用 with 进入，确保 lifespan 正常执行）。"""
    from fastapi.testclient import TestClient

    import app as application

    with TestClient(application.app) as test_client:
        yield test_client
