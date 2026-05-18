# debug_mcp_cpu_call.py
import asyncio

from app.agent.mcp_client import get_mcp_client_with_retry
from app.agent.aiops.utils import invoke_tool


async def main():
    client = await get_mcp_client_with_retry(force_new=True)
    tools = await client.get_tools()
    tool_map = {tool.name: tool for tool in tools}

    cpu_tool = tool_map["get_cpu_summary"]
    process_tool = tool_map["list_top_cpu_processes"]

    cpu_result = await invoke_tool(cpu_tool, {})
    process_result = await invoke_tool(process_tool, {"limit": 10})

    print("CPU SUMMARY:")
    print(cpu_result)

    print("\nTOP CPU PROCESSES:")
    print(process_result)


if __name__ == "__main__":
    asyncio.run(main())