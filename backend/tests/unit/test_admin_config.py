"""admin/config 端点端到端：PUT → GET 屏蔽 → invalidate factory。"""

from app.services.admin import config_store


def test_get_config_initial(client):
    resp = client.get("/api/v1/admin/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "llm" in body
    assert "embedding" in body
    assert "available_llm_providers" in body
    assert {"deepseek", "anthropic", "ollama", "openai_compat"}.issubset(set(body["available_llm_providers"]))


def test_put_llm_then_get_masked(client):
    resp = client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "deepseek",
            "api_key": "sk-mytestkey-abcdef",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "temperature": 0.2,
            "max_tokens": 1024,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    # 落盘后 raw 值是加密的
    raw = config_store.get_many_sync(["llm.api_key"], mask=False)["llm.api_key"]
    assert raw == "sk-mytestkey-abcdef"  # 解密后还原

    # GET 时屏蔽
    body = client.get("/api/v1/admin/config").json()
    assert body["llm"]["provider"] == "deepseek"
    assert body["llm"]["model"] == "deepseek-chat"
    assert "*" in body["llm"]["api_key"]
    assert body["llm"]["api_key"].startswith("sk-m")


def test_put_llm_empty_api_key_keeps_existing(client):
    # 先存一个 key
    client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "deepseek",
            "api_key": "sk-original-12345",
            "base_url": "",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 2048,
        },
    )
    # 再 PUT 一次但 api_key=""（前端只想改 model）
    client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "deepseek",
            "api_key": "",
            "base_url": "",
            "model": "deepseek-reasoner",
            "temperature": 0.3,
            "max_tokens": 2048,
        },
    )
    raw = config_store.get_sync("llm.api_key")
    assert raw == "sk-original-12345"
    assert config_store.get_sync("llm.model") == "deepseek-reasoner"


def test_put_llm_masked_api_key_is_ignored(client):
    # 先存一个
    client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "deepseek",
            "api_key": "sk-original-12345",
            "base_url": "",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 2048,
        },
    )
    # 模拟前端拿到屏蔽串后又回写
    client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "deepseek",
            "api_key": "sk-o*************",
            "base_url": "",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 2048,
        },
    )
    assert config_store.get_sync("llm.api_key") == "sk-original-12345"


def test_put_unknown_provider_rejected(client):
    resp = client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "totally-fake",
            "api_key": "x",
            "base_url": "",
            "model": "y",
            "temperature": 0.3,
            "max_tokens": 1024,
        },
    )
    assert resp.status_code == 422


def test_test_provider_without_key_returns_ok_false(client):
    # 先确保 LLM provider 是 deepseek 且没 key
    client.put(
        "/api/v1/admin/config/llm",
        json={
            "provider": "deepseek",
            "api_key": "",
            "base_url": "",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 1024,
        },
    )
    resp = client.post("/api/v1/admin/config/test", json={"target": "llm"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "API key" in (body["error"] or "")


def test_test_embedding_returns_not_implemented(client):
    # embedding provider 还没真实现 → 返回 ok=false 但不崩
    resp = client.post("/api/v1/admin/config/test", json={"target": "embedding"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False


# ===== 工具配置端点(Agentic 改造 §7) =====


def test_put_tavily_then_get_masked_and_breaker_cleared(client):
    from app.services.tools.base import breaker

    breaker.trip("web_search", reason="test")
    resp = client.put("/api/v1/admin/config/tavily", json={"api_key": "tvly-testkey-123456"})
    assert resp.status_code == 200, resp.text
    assert config_store.get_sync("tavily.api_key") == "tvly-testkey-123456"
    # 保存新 key 顺带清熔断
    assert breaker.is_open("web_search") is False

    body = client.get("/api/v1/admin/config/tavily").json()
    assert "*" in body["api_key"]
    assert body["api_key"].startswith("tvly")


def test_put_tavily_masked_key_ignored(client):
    client.put("/api/v1/admin/config/tavily", json={"api_key": "tvly-original-1"})
    client.put("/api/v1/admin/config/tavily", json={"api_key": "tvly*********"})
    assert config_store.get_sync("tavily.api_key") == "tvly-original-1"


def test_put_imagegen_roundtrip(client):
    resp = client.put(
        "/api/v1/admin/config/imagegen",
        json={
            "base_url": "https://img.example.com/v1",
            "api_key": "sk-img-123",
            "model": "gpt-image-1",
            "size": "1024x1024",
        },
    )
    assert resp.status_code == 200, resp.text
    assert config_store.get_sync("imagegen.base_url") == "https://img.example.com/v1"
    assert config_store.get_sync("imagegen.api_key") == "sk-img-123"

    body = client.get("/api/v1/admin/config/imagegen").json()
    assert body["base_url"] == "https://img.example.com/v1"
    assert body["model"] == "gpt-image-1"
    assert body["size"] == "1024x1024"
    assert "*" in body["api_key"]


def test_put_zhihuitu_roundtrip(client):
    resp = client.put(
        "/api/v1/admin/config/zhihuitu", json={"base_url": "https://draw.example.com"}
    )
    assert resp.status_code == 200, resp.text
    body = client.get("/api/v1/admin/config/zhihuitu").json()
    assert body["base_url"] == "https://draw.example.com"


def test_config_test_tavily_without_key(client, monkeypatch):
    # 强制视为未配置,隔离其他测试写入的值,也避免真实网络调用
    monkeypatch.setattr(config_store, "get_sync", lambda key, default="": "")
    resp = client.post("/api/v1/admin/config/test", json={"target": "tavily"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "未配置" in (body["error"] or "")


def test_config_test_imagegen_missing_config(client, monkeypatch):
    monkeypatch.setattr(config_store, "get_sync", lambda key, default="": "")
    resp = client.post("/api/v1/admin/config/test", json={"target": "imagegen"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "配置缺失" in (body["error"] or "")


def test_imagegen_api_root_strips_v1():
    from app.api.v1.admin import _imagegen_api_root

    assert _imagegen_api_root("https://www.mhcoding.ai/v1") == "https://www.mhcoding.ai"
    assert _imagegen_api_root("https://www.mhcoding.ai/") == "https://www.mhcoding.ai"
