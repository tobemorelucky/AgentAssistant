from langchain_core.documents import Document

from app.agent.aiops.tool_registry import get_aiops_local_tools
from app.agent.aiops.utils import unwrap_tool_result
from app.config import config


def test_get_aiops_local_tools_omits_web_search_when_disabled():
    original_enabled = config.web_search_enabled
    original_key = config.tavily_api_key
    try:
        config.web_search_enabled = False
        config.tavily_api_key = ""
        tool_names = [tool.name for tool in get_aiops_local_tools()]
        assert "web_search" not in tool_names
    finally:
        config.web_search_enabled = original_enabled
        config.tavily_api_key = original_key


def test_unwrap_tool_result_normalizes_content_and_artifact_tuple():
    result = unwrap_tool_result(
        (
            "mock content",
            [Document(page_content="doc body", metadata={"source": "https://example.com", "title": "Doc"})],
        )
    )
    assert result["content"] == "mock content"
    assert result["artifacts"][0]["metadata"]["source"] == "https://example.com"
