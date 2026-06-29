"""MCP Client Manager for NEO.

Dynamically loads and manages connections to open-source MCP (Model Context Protocol) servers.
This allows NEO to seamlessly interact with GitHub, Google Drive, Postgres, and any other MCP server.
"""

from __future__ import annotations

import os
import json
import logging
import asyncio
import threading
from pathlib import Path
from typing import Any

from core.paths import base_dir

logger = logging.getLogger(__name__)

# Try to import MCP; if not installed, we silently fail/disable
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


class MCPManager:
    """Manages long-lived MCP server processes and their tool metadata."""

    def __init__(self) -> None:
        self.config_path = base_dir() / "mcp.json"
        self.cache_path = base_dir() / "mcp_cache.json"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._servers: dict[str, dict] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, Any] = {}
        self._ready_event = threading.Event()
        self._exit_contexts = {}

    def start(self) -> None:
        """Start the MCP manager background thread."""
        if not MCP_AVAILABLE:
            logger.debug("[MCP] MCP library not installed. Skipping MCP manager.")
            return

        if not self.config_path.exists():
            logger.debug(f"[MCP] Config {self.config_path} not found. Skipping.")
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._servers = data.get("mcpServers", {})
        except Exception as e:
            logger.error(f"[MCP] Failed to parse {self.config_path}: {e}")
            return

        if not self._servers:
            logger.debug("[MCP] No MCP servers configured.")
            return

        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    self._tools = json.load(f)
                    logger.info(f"[MCP] Loaded {len(self._tools)} tools from cache.")
            except Exception as e:
                logger.error(f"[MCP] Failed to load cache: {e}")

        self._thread = threading.Thread(target=self._run_loop, name="MCPManager", daemon=True)
        self._thread.start()
        # Wait up to 5 seconds for servers to connect and tools to load
        self._ready_event.wait(timeout=5.0)
        logger.info(f"[MCP] Loaded {len(self._tools)} tools from {len(self._sessions)} servers.")

    def _run_loop(self) -> None:
        """Run the asyncio event loop for MCP clients."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        try:
            self._loop.run_until_complete(self._connect_all())
        except Exception as e:
            logger.error(f"[MCP] Error in MCP connection loop: {e}")
        finally:
            self._ready_event.set()  # Ensure we unblock the main thread

        # Keep the loop running to process futures
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _connect_all(self) -> None:
        """Connect to all configured servers concurrently."""
        tasks = []
        for name, config in self._servers.items():
            tasks.append(self._connect_server(name, config))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        # Save cache
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._tools, f)
        except Exception as e:
            logger.error(f"[MCP] Failed to write cache: {e}")
        
        self._ready_event.set()

    async def _connect_server(self, name: str, config: dict) -> None:
        """Connect to a single MCP server."""
        cmd = config.get("command")
        args = config.get("args", [])
        env = config.get("env", {})
        
        if not cmd:
            logger.warning(f"[MCP] Server {name} missing 'command'.")
            return

        # Prepare env by combining host env with config env
        full_env = os.environ.copy()
        for k, v in env.items():
            full_env[k] = str(v)

        logger.info(f"[MCP] Connecting to server: {name} ({cmd} {' '.join(args)})")
        
        try:
            server_params = StdioServerParameters(
                command=cmd,
                args=args,
                env=full_env
            )
            
            # stdio_client is an async context manager
            stdio_ctx = stdio_client(server_params)
            read, write = await stdio_ctx.__aenter__()
            self._exit_contexts[name] = stdio_ctx

            session = ClientSession(read, write)
            await session.__aenter__()
            self._sessions[name] = session
            
            # Initialize connection
            await session.initialize()
            
            # Fetch tools
            response = await session.list_tools()
            tools_list = response.tools
            
            for tool in tools_list:
                # Store the tool schema prefixed with the server name to avoid collisions
                tool_key = f"mcp__{name}__{tool.name}"
                
                # Convert MCP JSONSchema to NEO tool schema
                properties = tool.inputSchema.get("properties", {})
                required = tool.inputSchema.get("required", [])
                
                self._tools[tool_key] = {
                    "agent": "tool",
                    "category": "mcp",
                    "fast": False,
                    "description": f"[MCP: {name}] {tool.description or 'No description'}",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": properties,
                        "required": required
                    },
                    "_mcp_server": name,
                    "_mcp_tool_name": tool.name
                }
                
            logger.info(f"[MCP] Server {name} connected, loaded {len(tools_list)} tools.")
            
        except Exception as e:
            logger.error(f"[MCP] Failed to connect server {name}: {e}")

    def get_tools(self) -> dict[str, Any]:
        """Return the dictionary of loaded MCP tools."""
        return self._tools

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Synchronous wrapper to call an MCP tool."""
        if not self._loop:
            return f"Error: MCP manager loop not running."
            
        if server_name not in self._sessions:
            return f"Error: MCP server '{server_name}' not connected."
            
        # Schedule the call on the async loop and wait for result
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(server_name, tool_name, arguments),
            self._loop
        )
        try:
            return future.result(timeout=60.0)
        except Exception as e:
            return f"Error calling MCP tool: {str(e)}"

    async def _call_tool_async(self, server_name: str, tool_name: str, arguments: dict) -> str:
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError("Session not found")
            
        result = await session.call_tool(tool_name, arguments=arguments)
        
        # Format the MCP CallToolResult to text
        output = []
        if getattr(result, "content", None):
            for content in result.content:
                if content.type == "text":
                    output.append(content.text)
                elif content.type == "resource":
                    output.append(f"[Resource {content.resource.uri}]")
                else:
                    output.append(str(content))
                    
        if getattr(result, "isError", False):
            output.insert(0, "[ERROR]")
            
        return "\n".join(output) if output else "Task completed successfully (no output)."


# Global singleton instance
_manager: MCPManager | None = None

def get_mcp_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager

def get_mcp_tools() -> dict[str, Any]:
    """Returns all loaded MCP tools."""
    return get_mcp_manager().get_tools()

def execute_mcp_tool(tool_name: str, **kwargs) -> str:
    """Executes a tool where tool_name is formatted as mcp__server_name__tool_name."""
    if not tool_name.startswith("mcp__"):
        return f"Error: Invalid MCP tool name {tool_name}"
        
    parts = tool_name.split("__", 2)
    if len(parts) != 3:
        return f"Error: Malformed MCP tool name {tool_name}"
        
    _, server_name, actual_tool = parts
    return get_mcp_manager().call_tool(server_name, actual_tool, kwargs)
