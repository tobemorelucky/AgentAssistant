# debug_mcp_tools.py
import asyncio

from app.agent.mcp_client import get_mcp_client_with_retry


async def main():
    client = await get_mcp_client_with_retry(force_new=True)
    tools = await client.get_tools()

    print("TOOLS:")
    for tool in tools:
        print("-", getattr(tool, "name", str(tool)))


if __name__ == "__main__":
    asyncio.run(main())