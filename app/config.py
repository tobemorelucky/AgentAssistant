"""配置管理模块.

使用 Pydantic Settings 实现类型安全的配置管理。
"""

from typing import Any, Dict
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "SuperBizAgent"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # 问答模型（LLM）配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-max"
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_model: str = ""

    # 向量模型（Embedding）配置
    embedding_api_key: str = ""
    embedding_api_base: str = ""
    embedding_mode: str = ""
    text_embedding_model: str = ""
    multimodal_embedding_model: str = ""
    embedding_model: str = ""  # 兼容旧的通用配置：EMBEDDING_MODEL
    embedding_dimensions: int = 0

    # DashScope Embedding 兼容配置（保留向后兼容）
    dashscope_embedding_mode: str = "single_modal"
    dashscope_text_embedding_model: str = "text-embedding-v4"
    dashscope_multimodal_embedding_model: str = "tongyi-embedding-vision-flash-2026-03-06"
    dashscope_embedding_model: str = ""  # 兼容旧配置：DASHSCOPE_EMBEDDING_MODEL
    dashscope_embedding_dimensions: int = 1024

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    rag_model: str = "qwen-max"  # 使用快速响应模型，不带扩展思考

    # 文档分块配置
    aiops_max_steps: int = 8
    aiops_allow_legacy_generic_diagnosis: bool = False
    web_search_enabled: bool = False
    tavily_api_key: str = ""
    web_search_max_results: int = 5
    web_search_depth: str = "basic"
    web_search_timeout: float = 10.0
    aiops_monitor_provider: str = "mock"
    aiops_alert_provider: str = "mock"
    aiops_remote_host_base_url: str = ""
    aiops_remote_host_token: str = ""
    aiops_heartbeat_enabled: bool = False
    aiops_heartbeat_interval_minutes: int = 60
    aiops_heartbeat_trigger_deep_diagnosis: bool = True
    aiops_heartbeat_store_report: bool = True
    aiops_heartbeat_max_concurrent_runs: int = 1
    aiops_session_memory_enabled: bool = True
    aiops_session_memory_debug_api: bool = False
    aiops_session_memory_backend: str = "file"
    aiops_session_memory_window: int = 20
    aiops_session_memory_summarize_batch: int = 15
    aiops_session_memory_max_turn_chars: int = 4000
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # MCP 服务配置
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"

    @property
    def mcp_servers(self) -> Dict[str, Dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            }
        }

    @staticmethod
    def is_multimodal_embedding_model(model_name: str) -> bool:
        """根据模型名判断是否为多模态/视觉 embedding 模型。"""
        normalized = model_name.strip().lower()
        multimodal_markers = ("vision", "multimodal", "multi-modal", "multi_modal", "image")
        return any(marker in normalized for marker in multimodal_markers)

    def get_embedding_mode(self) -> str:
        """标准化 embedding 模式。"""
        candidate = self.embedding_mode or self.dashscope_embedding_mode
        normalized = candidate.strip().lower()
        if normalized in {"multimodal", "multi-modal", "multi_modal"}:
            return "multimodal"
        return "single_modal"

    def get_text_embedding_model(self) -> str:
        """获取文本 embedding 模型名。"""
        candidate = (
            self.text_embedding_model
            or self.dashscope_text_embedding_model
            or self.embedding_model
            or self.dashscope_embedding_model
        )
        return candidate.strip() or "text-embedding-v4"

    def get_multimodal_embedding_model(self) -> str:
        """获取多模态 embedding 模型名。"""
        candidate = self.multimodal_embedding_model or self.dashscope_multimodal_embedding_model
        return candidate.strip()

    def get_embedding_api_key(self) -> str:
        """获取 embedding 链路使用的 API Key。"""
        candidate = self.embedding_api_key or self.dashscope_api_key
        return candidate.strip()

    def get_embedding_api_base(self) -> str:
        """获取 embedding 链路使用的 API Base。"""
        candidate = self.embedding_api_base or self.dashscope_api_base
        return candidate.strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def get_embedding_dimensions(self) -> int:
        """获取 embedding 维度配置。"""
        return self.embedding_dimensions or self.dashscope_embedding_dimensions or 1024

    def get_llm_api_key(self) -> str:
        """Return the API key used by chat and AIOps models."""
        candidate = self.llm_api_key or self.dashscope_api_key
        return candidate.strip()

    def get_llm_api_base(self) -> str:
        """Return the API base used by chat and AIOps models."""
        candidate = self.llm_api_base or self.dashscope_api_base
        return candidate.strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def get_llm_model(self, preferred_model: str = "") -> str:
        """Return the concrete model used by chat and AIOps models."""
        for candidate in (
            self.llm_model,
            self.dashscope_model,
            preferred_model,
            self.rag_model,
            "qwen-max",
        ):
            normalized = (candidate or "").strip()
            if normalized:
                return normalized
        return "qwen-max"

    def get_validated_text_embedding_model(self) -> str:
        """获取并校验文本 embedding 模型。"""
        model = self.get_text_embedding_model()
        if self.is_multimodal_embedding_model(model):
            raise ValueError(
                "当前文本向量链路仅支持文本 embedding 模型。"
                f"检测到多模态模型: {model}。"
                "请在 .env 中将 TEXT_EMBEDDING_MODEL 设置为文本模型"
                "（例如 text-embedding-v4），"
                "并把视觉模型放到 MULTIMODAL_EMBEDDING_MODEL。"
            )
        return model

    def get_selected_embedding_model(self) -> str:
        """根据 embedding 模式返回当前激活的模型名。"""
        if self.get_embedding_mode() == "multimodal":
            return self.get_multimodal_embedding_model()
        return self.get_validated_text_embedding_model()


# 全局配置实例
config = Settings()
