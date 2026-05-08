"""Shared AIOps utility helpers."""

from __future__ import annotations

import json
from typing import Any, List


def format_tools_description(tools: List) -> str:
    """Render tool descriptions for prompts."""
    tool_descriptions = []
    for tool in tools:
        if hasattr(tool, "name") and hasattr(tool, "description"):
            tool_descriptions.append(f"- {tool.name}: {tool.description}")
    return "\n".join(tool_descriptions)


async def invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    """Invoke a LangChain/MCP/local tool with best-effort compatibility."""
    if hasattr(tool, "ainvoke"):
        result = await tool.ainvoke(args)
    elif hasattr(tool, "invoke"):
        result = tool.invoke(args)
    elif callable(tool):
        result = tool(**args)
    else:
        raise RuntimeError(f"Tool '{getattr(tool, 'name', tool)}' is not invokable")
    return unwrap_tool_result(result)


def unwrap_tool_result(value: Any) -> Any:
    """Best-effort normalization for MCP/LangChain content blocks."""
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            return unwrap_tool_result(json.loads(text))
        except json.JSONDecodeError:
            embedded = _extract_embedded_json(text)
            if embedded is not None:
                return unwrap_tool_result(embedded)
            return text

    if isinstance(value, list):
        if value and all(isinstance(item, dict) and "text" in item for item in value):
            combined = "\n".join(str(item.get("text", "")) for item in value if item.get("text"))
            return unwrap_tool_result(combined)
        return [unwrap_tool_result(item) for item in value]

    if isinstance(value, dict):
        if value.get("type") == "text" and "text" in value:
            return unwrap_tool_result(value.get("text", ""))
        if "structuredContent" in value:
            return unwrap_tool_result(value["structuredContent"])
        if "content" in value and len(value) <= 2:
            return unwrap_tool_result(value["content"])
        return {
            key: unwrap_tool_result(item)
            for key, item in value.items()
            if key not in {"id", "type"}
        }

    return value


def _extract_embedded_json(text: str) -> Any:
    text_index = text.find('"text"')
    if text_index >= 0:
        colon_index = text.find(":", text_index)
        quote_index = text.find('"', colon_index + 1)
        if colon_index >= 0 and quote_index >= 0:
            chars: list[str] = []
            escaped = False
            for char in text[quote_index + 1 :]:
                if escaped:
                    chars.append(char)
                    escaped = False
                    continue
                if char == "\\":
                    chars.append(char)
                    escaped = True
                    continue
                if char == '"':
                    break
                chars.append(char)
            candidate = "".join(chars).replace('\\"', '"')
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = text[brace_start : brace_end + 1].replace('\\"', '"')
        candidate = candidate.rstrip('"}] ')
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None
