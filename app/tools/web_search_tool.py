"""AIOps-only web search tool backed by Tavily."""

from __future__ import annotations

from typing import List, Tuple

import httpx
from langchain_core.documents import Document
from langchain_core.tools import tool
from loguru import logger

from app.config import config


def _format_search_result(index: int, title: str, url: str, content: str) -> str:
    return (
        f"〖联网资料 {index}〗\n"
        f"标题: {title or '未提供标题'}\n"
        f"链接: {url or '未提供链接'}\n"
        f"内容: {content or '未提供摘要'}"
    )


def _document_from_result(title: str, url: str, content: str, score: float | None) -> Document:
    return Document(
        page_content=content or "",
        metadata={
            "source": url or "",
            "title": title or "",
            "score": score,
            "provider": "tavily",
        },
    )


@tool("web_search", response_format="content_and_artifact")
async def web_search(query: str) -> Tuple[str, List[Document]]:
    """Search public web sources for official docs or public troubleshooting references."""
    if not config.web_search_enabled:
        return "联网搜索未启用。", []
    if not config.tavily_api_key:
        return "联网搜索未配置 Tavily API Key。", []

    payload = {
        "api_key": config.tavily_api_key,
        "query": query,
        "max_results": config.web_search_max_results,
        "search_depth": config.web_search_depth,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }

    try:
        async with httpx.AsyncClient(timeout=config.web_search_timeout) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            if response.status_code != 200:
                logger.warning(
                    "Tavily search failed with status {} for query={!r}",
                    response.status_code,
                    query,
                )
                return f"Tavily 搜索失败，HTTP {response.status_code}: {response.text}", []
            data = response.json()
    except httpx.TimeoutException:
        logger.warning("Tavily search timed out for query={!r}", query)
        return "联网搜索超时，请稍后重试。", []
    except httpx.HTTPError as exc:
        logger.warning("Tavily HTTP error for query={!r}: {}", query, exc)
        return f"联网搜索请求失败: {exc}", []
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected Tavily search error for query={!r}", query)
        return f"联网搜索异常: {exc}", []

    results = data.get("results") or []
    if not results:
        return "未找到联网搜索结果", []

    documents: list[Document] = []
    formatted_results: list[str] = []
    for index, item in enumerate(results, start=1):
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        content = str(item.get("content") or item.get("snippet") or "")
        score = item.get("score")
        documents.append(_document_from_result(title, url, content, score))
        formatted_results.append(_format_search_result(index, title, url, content))

    return "\n\n".join(formatted_results), documents
