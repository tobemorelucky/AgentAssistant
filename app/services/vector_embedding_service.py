"""向量嵌入服务模块 - 基于 LangChain Embeddings 标准接口。"""

from typing import List

from langchain_core.embeddings import Embeddings
from openai import OpenAI
from loguru import logger

from app.config import config


class OpenAICompatibleEmbeddings(Embeddings):
    """OpenAI 兼容模式的文本 Embedding 封装。
    
    实现 LangChain 标准 Embeddings 接口:
    - embed_documents(texts: List[str]) → List[List[float]]: 批量嵌入文档
    - embed_query(text: str) → List[float]: 嵌入单个查询
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-v4",
        dimensions: int = 1024,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        """
        初始化 Embeddings 客户端。
        
        Args:
            api_key: Embedding API Key
            model: 嵌入模型名称
            dimensions: 向量维度
            base_url: OpenAI 兼容模式 API 地址
        """
        if not api_key or api_key == "your-api-key-here":
            raise ValueError("请设置环境变量 EMBEDDING_API_KEY（未配置时会回退到 DASHSCOPE_API_KEY）")
        if config.is_multimodal_embedding_model(model):
            raise ValueError(
                "当前文档索引/检索链路使用的是 OpenAI 兼容文本 Embedding 接口，"
                f"不支持多模态模型 {model}。"
                "请将 .env 中的 TEXT_EMBEDDING_MODEL 配置为文本模型，"
                "并把视觉向量模型放到 MULTIMODAL_EMBEDDING_MODEL。"
            )
        
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model
        self.dimensions = dimensions
        self.base_url = base_url
        
        # 打印初始化信息
        masked_key = self._mask_api_key(api_key)
        logger.info(
            f"OpenAI Compatible Embeddings 初始化完成 - "
            f"模型: {model}, 维度: {dimensions}, Base URL: {base_url}, API Key: {masked_key}"
        )

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """掩码 API Key 用于日志"""
        if len(api_key) > 8:
            return f"{api_key[:8]}...{api_key[-4:]}"
        return "***"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量嵌入文档列表 (LangChain 标准接口)
        
        Args:
            texts: 文本列表
            
        Returns:
            List[List[float]]: 嵌入向量列表
        """
        if not texts:
            return []
        
        try:
            logger.info(f"批量嵌入 {len(texts)} 个文档")
            
            # 批量调用 API
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float"
            )
            
            embeddings = [item.embedding for item in response.data]
            logger.debug(f"批量嵌入完成, 维度: {len(embeddings[0])}")
            
            return embeddings
            
        except Exception as e:
            logger.error(f"批量嵌入失败: {e}")
            raise RuntimeError(f"批量嵌入失败: {e}") from e

    def embed_query(self, text: str) -> List[float]:
        """
        嵌入单个查询文本 (LangChain 标准接口)
        
        Args:
            text: 查询文本
            
        Returns:
            List[float]: 嵌入向量
        """
        if not text or not text.strip():
            raise ValueError("查询文本不能为空")
        
        try:
            logger.debug(f"嵌入查询, 长度: {len(text)} 字符")
            
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
                dimensions=self.dimensions,
                encoding_format="float"
            )
            
            embedding = response.data[0].embedding
            logger.debug(f"查询嵌入完成, 维度: {len(embedding)}")
            
            return embedding
            
        except Exception as e:
            logger.error(f"查询嵌入失败: {e}")
            raise RuntimeError(f"查询嵌入失败: {e}") from e


# 全局单例
vector_embedding_service = OpenAICompatibleEmbeddings(
    api_key=config.get_embedding_api_key(),
    model=config.get_validated_text_embedding_model(),
    dimensions=config.get_embedding_dimensions(),
    base_url=config.get_embedding_api_base(),
)
