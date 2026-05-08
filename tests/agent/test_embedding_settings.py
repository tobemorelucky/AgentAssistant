from app.config import Settings


def test_embedding_mode_defaults_to_single_modal():
    settings = Settings(dashscope_embedding_mode="unexpected")

    assert settings.get_embedding_mode() == "single_modal"


def test_text_embedding_model_prefers_new_env_var():
    settings = Settings(
        text_embedding_model="text-embedding-v4",
        dashscope_text_embedding_model="legacy-dashscope-text-model",
        dashscope_embedding_model="legacy-text-model",
    )

    assert settings.get_text_embedding_model() == "text-embedding-v4"


def test_text_embedding_model_falls_back_to_legacy_env_var():
    settings = Settings(
        dashscope_text_embedding_model="",
        dashscope_embedding_model="text-embedding-v3",
    )

    assert settings.get_text_embedding_model() == "text-embedding-v3"


def test_embedding_api_key_and_base_can_override_llm_config():
    settings = Settings(
        embedding_api_key="embedding-key",
        embedding_api_base="https://ark.cn-beijing.volces.com/api/v3",
        dashscope_api_key="llm-key",
        dashscope_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert settings.get_embedding_api_key() == "embedding-key"
    assert settings.get_embedding_api_base() == "https://ark.cn-beijing.volces.com/api/v3"


def test_embedding_api_key_and_base_fallback_to_llm_config():
    settings = Settings(
        embedding_api_key="",
        embedding_api_base="",
        dashscope_api_key="llm-key",
        dashscope_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert settings.get_embedding_api_key() == "llm-key"
    assert settings.get_embedding_api_base() == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_multimodal_model_is_rejected_for_text_embedding():
    settings = Settings(
        text_embedding_model="tongyi-embedding-vision-flash-2026-03-06",
    )

    try:
        settings.get_validated_text_embedding_model()
    except ValueError as exc:
        assert "TEXT_EMBEDDING_MODEL" in str(exc)
    else:
        raise AssertionError("Expected ValueError for multimodal text embedding model")
