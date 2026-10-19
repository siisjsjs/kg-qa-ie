"""config_store：往返 + 加密字段隔离。"""

from app.services.admin import config_store


def test_set_and_get_plain():
    config_store.set_value_sync("llm.model", "deepseek-chat")
    assert config_store.get_sync("llm.model") == "deepseek-chat"


def test_set_and_get_encrypted_returns_plain():
    config_store.set_value_sync("llm.api_key", "sk-secret-123", encrypted=True)
    assert config_store.get_sync("llm.api_key") == "sk-secret-123"


def test_get_many_mask_only_encrypted():
    config_store.set_many_sync(
        [
            {"key": "llm.api_key", "value": "sk-secret-12345", "encrypted": True},
            {"key": "llm.model", "value": "deepseek-chat", "encrypted": False},
        ]
    )
    masked = config_store.get_many_sync(["llm.api_key", "llm.model"], mask=True)
    assert masked["llm.api_key"].startswith("sk-s") and "*" in masked["llm.api_key"]
    assert masked["llm.model"] == "deepseek-chat"

    plain = config_store.get_many_sync(["llm.api_key", "llm.model"], mask=False)
    assert plain["llm.api_key"] == "sk-secret-12345"


def test_missing_key_returns_default():
    assert config_store.get_sync("nope.does.not.exist") is None
    assert config_store.get_sync("nope.x", default="fallback") == "fallback"


def test_persistence_across_reset():
    config_store.set_value_sync("llm.model", "persisted-model")
    config_store.reset_cache()
    assert config_store.get_sync("llm.model") == "persisted-model"
