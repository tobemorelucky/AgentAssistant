"""Shared AIOps utility helpers."""

from __future__ import annotations

import json
from typing import Any, List

from langchain_core.documents import Document


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

    if isinstance(value, tuple) and len(value) == 2:
        content, artifact = value
        return {
            "content": unwrap_tool_result(content),
            "artifacts": _normalize_artifact(artifact),
        }

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

    if isinstance(value, Document):
        return {
            "page_content": value.page_content,
            "metadata": dict(value.metadata or {}),
        }

    return value


def normalize_external_reference_result(raw_result: Any) -> dict[str, Any]:
    """Normalize web/external search results into a stable structured payload."""
    parsed = unwrap_tool_result(raw_result)

    if isinstance(parsed, dict):
        if parsed.get("ok") is False or parsed.get("error") or parsed.get("error_code"):
            return {
                "ok": False,
                "content": str(parsed.get("content") or "").strip(),
                "artifacts": _coerce_external_artifacts(parsed.get("artifacts") or parsed.get("results") or []),
                "source": "external_reference",
                "message": str(parsed.get("message") or parsed.get("error") or "External search failed."),
                "error_code": str(parsed.get("error_code") or "external_reference_error"),
            }

        content = str(parsed.get("content") or parsed.get("answer") or "").strip()
        artifacts = _coerce_external_artifacts(parsed.get("artifacts") or parsed.get("results") or [])
        if not content and artifacts:
            content = _artifacts_to_content(artifacts)
        return {
            "ok": bool(content or artifacts),
            "content": content,
            "artifacts": artifacts,
            "source": "external_reference",
            "message": "" if (content or artifacts) else "External search returned no usable content.",
            "error_code": "" if (content or artifacts) else "invalid_external_reference",
        }

    if isinstance(parsed, str):
        content = parsed.strip()
        return {
            "ok": bool(content),
            "content": content,
            "artifacts": [],
            "source": "external_reference",
            "message": "" if content else "External search returned empty text.",
            "error_code": "" if content else "invalid_external_reference",
        }

    if isinstance(parsed, list):
        artifacts = _coerce_external_artifacts(parsed)
        content = _artifacts_to_content(artifacts)
        return {
            "ok": bool(content or artifacts),
            "content": content,
            "artifacts": artifacts,
            "source": "external_reference",
            "message": "" if (content or artifacts) else "External search returned an empty result list.",
            "error_code": "" if (content or artifacts) else "invalid_external_reference",
        }

    return {
        "ok": False,
        "content": "",
        "artifacts": [],
        "source": "external_reference",
        "message": "web_search did not return a structured payload.",
        "error_code": "invalid_external_reference",
    }


def _normalize_artifact(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_artifact(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_artifact(item) for item in value]
    if isinstance(value, Document):
        return {
            "page_content": value.page_content,
            "metadata": dict(value.metadata or {}),
        }
    return unwrap_tool_result(value)


def _coerce_external_artifacts(value: Any) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        normalized = _normalize_artifact(item)
        if isinstance(normalized, dict):
            if "page_content" in normalized:
                artifacts.append(normalized)
                continue
            title = normalized.get("title") or normalized.get("name") or ""
            source = normalized.get("source") or normalized.get("url") or normalized.get("link") or ""
            content = normalized.get("content") or normalized.get("snippet") or normalized.get("summary") or ""
            metadata = dict(normalized.get("metadata") or {})
            if title:
                metadata.setdefault("title", title)
            if source:
                metadata.setdefault("source", source)
            metadata.setdefault("provider", "external_reference")
            if content or metadata:
                artifacts.append({"page_content": str(content), "metadata": metadata})
                continue
        elif isinstance(normalized, str) and normalized.strip():
            artifacts.append(
                {
                    "page_content": normalized.strip(),
                    "metadata": {"provider": "external_reference"},
                }
            )
    return artifacts


def _artifacts_to_content(artifacts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, artifact in enumerate(artifacts[:3], start=1):
        if not isinstance(artifact, dict):
            continue
        metadata = artifact.get("metadata") or {}
        title = str(metadata.get("title") or f"外部参考 {index}")
        url = str(metadata.get("source") or "")
        content = str(artifact.get("page_content") or "").strip()
        block = [f"〖外部补充参考 {index}〗", f"标题: {title}"]
        if url:
            block.append(f"链接: {url}")
        if content:
            block.append(f"内容: {content}")
        lines.append("\n".join(block))
    return "\n\n".join(lines).strip()


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
