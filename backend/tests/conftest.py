"""pytest 共享 fixture。

每个测试自动用临时 cache_dir，隔离 system_config.json 与 fernet key，
避免污染真实数据。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """把 settings.cache_dir / wiki_root 指到 tmp_path，并重置内存单例。"""
    from app.core import config as cfg_module
    from app.core import crypto
    from app.services.admin import config_store
    from app.services.embedding import factory as emb_factory
    from app.services.llm import factory as llm_factory

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    wiki_root = tmp_path / "wiki"
    monkeypatch.setattr(cfg_module.settings, "cache_dir", str(cache_dir))
    monkeypatch.setattr(cfg_module.settings, "wiki_root", str(wiki_root))

    crypto.reset_for_tests()
    config_store.reset_cache()
    llm_factory.invalidate_llm_cache()
    emb_factory.invalidate_embedding_cache()

    yield


@pytest.fixture()
def client() -> TestClient:
    from app.main import app
    return TestClient(app)
