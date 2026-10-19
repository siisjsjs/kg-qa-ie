"""provider 工厂单元测试。"""

from app.services.embedding import list_providers as list_embedding_providers
from app.services.llm import list_providers as list_llm_providers


def test_llm_providers_registered():
    names = list_llm_providers()
    assert {"deepseek", "openai_compat", "ollama", "anthropic"}.issubset(set(names))


def test_embedding_providers_registered():
    names = list_embedding_providers()
    # 本地/Ollama/HF 桩已删除,只保留实际在用的 API 通道,避免误选未实现 provider 崩溃。
    assert names == ["openai_compat"]
