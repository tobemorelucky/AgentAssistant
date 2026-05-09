"""AIOps-only local tool registry."""

from __future__ import annotations

from loguru import logger

from app.config import config
from app.tools import get_current_time, retrieve_knowledge, web_search


def get_aiops_local_tools() -> list:
    """Return local tools available to the governed AIOps workflow only."""
    tools = [get_current_time, retrieve_knowledge]
    if config.web_search_enabled and config.tavily_api_key.strip():
        tools.append(web_search)
    elif config.web_search_enabled:
        logger.warning("WEB_SEARCH_ENABLED=true but TAVILY_API_KEY is empty; web_search will not be registered.")
    else:
        logger.debug("AIOps web_search is disabled by configuration.")
    return tools
