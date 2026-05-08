"""AIOps Agent 通用工具函数。"""

from typing import Any, List


def format_tools_description(tools: List) -> str:
    """格式化工具列表为描述文本"""
    tool_descriptions = []
    for tool in tools:
        if hasattr(tool, 'name') and hasattr(tool, 'description'):
            tool_descriptions.append(f"- {tool.name}: {tool.description}")
    return "\n".join(tool_descriptions)


async def invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    """Invoke a LangChain/MCP/local tool with best-effort compatibility."""
    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(args)
    if hasattr(tool, "invoke"):
        return tool.invoke(args)
    if callable(tool):
        return tool(**args)
    raise RuntimeError(f"Tool '{getattr(tool, 'name', tool)}' is not invokable")
