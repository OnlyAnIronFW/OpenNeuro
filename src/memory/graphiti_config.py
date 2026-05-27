"""Graphiti LLM / Embedder / Reranker 客户端工厂

LLM:      DeepSeek API (用户已有 key, 用于事实提取 + rerank)
Embedder: 硅基流动 SiliconFlow (免费 BAAI/bge-large-zh-v1.5)
"""

import os

from graphiti_core.cross_encoder import OpenAIRerankerClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient


def make_deepseek_llm_config() -> LLMConfig:
    return LLMConfig(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        model="deepseek-chat",
        small_model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        temperature=0.3,
        max_tokens=1024,
    )


def make_deepseek_client() -> OpenAIGenericClient:
    return OpenAIGenericClient(config=make_deepseek_llm_config())


def make_siliconflow_embedder() -> OpenAIEmbedder:
    return OpenAIEmbedder(config=OpenAIEmbedderConfig(
        api_key=os.environ.get("SILICONFLOW_API_KEY", ""),
        embedding_model="BAAI/bge-large-zh-v1.5",
        embedding_dim=1024,
        base_url="https://api.siliconflow.cn/v1",
    ))


def make_deepseek_reranker() -> OpenAIRerankerClient:
    """Reranker 复用 DeepSeek API (OpenAIRerankerClient 用 chat API + logprobs 做重排)"""
    return OpenAIRerankerClient(config=LLMConfig(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
    ))
