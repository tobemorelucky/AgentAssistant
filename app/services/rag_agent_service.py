"""RAG chat service built on LangGraph + MCP tools."""

from __future__ import annotations

from typing import Annotated, Any, AsyncGenerator, Dict, Sequence
from datetime import datetime

from langchain.agents import create_agent
from langchain_core.messages import BaseMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from loguru import logger
from typing_extensions import TypedDict

from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_factory import llm_factory
from app.services.rag_answer_guard import (
    build_rag_realtime_guard_answer,
    is_realtime_status_request_in_rag,
)
from app.tools import get_current_time, retrieve_knowledge


class AgentState(TypedDict):
    """Agent state for chat history management."""

    messages: Annotated[Sequence[BaseMessage], add_messages]


def trim_messages_middleware(state: AgentState) -> dict[str, Any] | None:
    """Keep the system prompt plus the latest conversation turns."""
    messages = state["messages"]
    if len(messages) <= 7:
        return None

    first_msg = messages[0]
    recent_messages = messages[-6:] if len(messages) % 2 == 0 else messages[-7:]
    new_messages = [first_msg] + list(recent_messages)

    logger.debug("Trimmed chat history: {} -> {} messages", len(messages), len(new_messages))
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *new_messages,
        ]
    }


class RagAgentService:
    """RAG chat service for standard assistant conversations."""

    def __init__(self, streaming: bool = True):
        self.model_name = config.get_llm_model(config.rag_model)
        self.streaming = streaming
        self.system_prompt = self._build_system_prompt()
        self.model = llm_factory.create_qwen_chat_model(
            preferred_model=self.model_name,
            temperature=0.7,
            streaming=streaming,
        )
        self.tools = [retrieve_knowledge, get_current_time]
        self.mcp_tools: list = []
        self.checkpointer = MemorySaver()
        self.agent = None
        self._agent_initialized = False

        logger.info(
            "RAG Agent service initialized, model={}, streaming={}",
            self.model_name,
            streaming,
        )

    async def _initialize_agent(self):
        """Load MCP tools lazily and create the chat agent once."""
        if self._agent_initialized:
            return

        mcp_client = await get_mcp_client_with_retry()
        self.mcp_tools = await mcp_client.get_tools()
        all_tools = self.tools + self.mcp_tools

        self.agent = create_agent(
            self.model,
            tools=all_tools,
            checkpointer=self.checkpointer,
        )
        self._agent_initialized = True

        tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in all_tools]
        logger.info("RAG Agent loaded {} tools: {}", len(tool_names), ", ".join(tool_names))

    def _build_system_prompt(self) -> str:
        return (
            "你是一个基于知识库和工具的智能运维助手。"
            "请优先使用本地知识库和可用工具回答问题；"
            "如果工具失败，需要如实说明；"
            "不要编造不存在的检索结果、日志或监控数据。"
        )

    async def query(self, question: str, session_id: str) -> str:
        """Run a non-streaming chat query."""
        try:
            if is_realtime_status_request_in_rag(question):
                logger.info("[session {}] RAG realtime guard triggered", session_id)
                return build_rag_realtime_guard_answer(question)

            await self._initialize_agent()
            logger.info("[session {}] RAG query started: {}", session_id, question)

            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=question),
            ]
            result = await self.agent.ainvoke(
                input={"messages": messages},
                config={"configurable": {"thread_id": session_id}},
            )

            messages_result = result.get("messages", [])
            if not messages_result:
                logger.warning("[session {}] RAG query returned no messages", session_id)
                return ""

            last_message = messages_result[-1]
            answer = last_message.content if hasattr(last_message, "content") else str(last_message)

            if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                tool_names = [tool_call.get("name", "unknown") for tool_call in last_message.tool_calls]
                logger.info("[session {}] RAG query tool calls: {}", session_id, tool_names)

            logger.info("[session {}] RAG query completed", session_id)
            return answer

        except Exception as exc:
            logger.error("[session {}] RAG query failed: {}", session_id, exc)
            raise

    async def query_stream(self, question: str, session_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Run a streaming chat query for the standard RAG chat endpoint."""
        try:
            if is_realtime_status_request_in_rag(question):
                logger.info("[session {}] RAG realtime guard triggered (stream)", session_id)
                guard_answer = build_rag_realtime_guard_answer(question)
                yield {"type": "content", "data": guard_answer, "node": "guard"}
                yield {"type": "complete", "data": {"answer": guard_answer}}
                return

            await self._initialize_agent()
            logger.info("[session {}] RAG streaming query started: {}", session_id, question)

            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=question),
            ]
            agent_input = {"messages": messages}
            config_dict = {"configurable": {"thread_id": session_id}}
            full_answer = ""

            async for token, metadata in self.agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = metadata.get("langgraph_node", "unknown") if isinstance(metadata, dict) else "unknown"
                message_type = type(token).__name__

                if message_type not in {"AIMessage", "AIMessageChunk"}:
                    continue

                content_blocks = getattr(token, "content_blocks", None)
                token_content = getattr(token, "content", "")

                emitted = False
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        if not isinstance(block, dict) or block.get("type") != "text":
                            continue
                        text_content = str(block.get("text", ""))
                        if not text_content:
                            continue
                        full_answer += text_content
                        emitted = True
                        yield {
                            "type": "content",
                            "data": text_content,
                            "node": node_name,
                        }

                if not emitted and isinstance(token_content, str) and token_content:
                    full_answer += token_content
                    yield {
                        "type": "content",
                        "data": token_content,
                        "node": node_name,
                    }

            logger.info("[session {}] query_stream full_answer length={}", session_id, len(full_answer))
            logger.info("[session {}] RAG streaming query completed", session_id)
            yield {"type": "complete", "data": {"answer": full_answer}}

        except Exception as exc:
            logger.error("[session {}] RAG streaming query failed: {}", session_id, exc)
            yield {
                "type": "error",
                "data": str(exc),
            }
            raise

    def get_session_history(self, session_id: str) -> list:
        """Return conversation history from the in-memory checkpointer."""
        try:
            checkpoint_tuple = self.checkpointer.get({"configurable": {"thread_id": session_id}})
            if not checkpoint_tuple:
                logger.info("[session {}] chat history size=0", session_id)
                return []

            checkpoint_data = (
                checkpoint_tuple.checkpoint
                if hasattr(checkpoint_tuple, "checkpoint")
                else checkpoint_tuple[0] if checkpoint_tuple else {}
            )
            messages = checkpoint_data.get("channel_values", {}).get("messages", [])

            history = []
            for msg in messages:
                if isinstance(msg, SystemMessage):
                    continue
                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                content = msg.content if hasattr(msg, "content") else str(msg)
                timestamp = getattr(msg, "timestamp", None) or datetime.now().isoformat()
                history.append(
                    {
                        "role": role,
                        "content": content,
                        "timestamp": str(timestamp),
                    }
                )

            logger.info("[session {}] chat history size={}", session_id, len(history))
            return history

        except Exception as exc:
            logger.error("[session {}] get_session_history failed: {}", session_id, exc)
            return []

    def clear_session(self, session_id: str) -> bool:
        """Clear chat history for one session."""
        try:
            self.checkpointer.delete_thread(session_id)
            logger.info("Cleared chat session {}", session_id)
            return True
        except Exception as exc:
            logger.error("Failed to clear chat session {}: {}", session_id, exc)
            return False

    async def cleanup(self):
        """Cleanup hook for app shutdown."""
        logger.info("Cleaning up RAG Agent service")


rag_agent_service = RagAgentService(streaming=True)
