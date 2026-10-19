"""factory invalidation：配置变更后返回新实例。"""

from app.services.admin import config_store
from app.services.llm import factory as llm_factory
from app.services.llm.deepseek import DeepSeekProvider


def test_factory_picks_up_config_changes():
    config_store.set_value_sync("llm.provider", "deepseek")
    config_store.set_value_sync("llm.model", "deepseek-chat")
    config_store.set_value_sync("llm.api_key", "sk-old", encrypted=True)
    llm_factory.invalidate_llm_cache()

    p1 = llm_factory.get_llm_provider()
    assert isinstance(p1, DeepSeekProvider)
    assert p1.api_key == "sk-old"

    config_store.set_value_sync("llm.api_key", "sk-new", encrypted=True)
    llm_factory.invalidate_llm_cache()

    p2 = llm_factory.get_llm_provider()
    assert p2.api_key == "sk-new"
    assert p2 is not p1


def test_factory_caches_when_config_unchanged():
    config_store.set_value_sync("llm.provider", "deepseek")
    config_store.set_value_sync("llm.model", "deepseek-chat")
    llm_factory.invalidate_llm_cache()
    p1 = llm_factory.get_llm_provider()
    p2 = llm_factory.get_llm_provider()
    assert p1 is p2  # 同签名复用


def test_factory_unknown_provider_raises():
    config_store.set_value_sync("llm.provider", "no-such-provider")
    llm_factory.invalidate_llm_cache()
    try:
        llm_factory.get_llm_provider()
    except ValueError as e:
        assert "no-such-provider" in str(e)
    else:
        raise AssertionError("expected ValueError")
