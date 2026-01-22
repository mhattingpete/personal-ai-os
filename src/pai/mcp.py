"""MCP client manager for PAI.

Handles MCP server lifecycle, tool discovery, and tool execution.
All connector interactions go through MCP after migration.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel, Field

from pai.config import get_config_dir


# =============================================================================
# Configuration Models
# =============================================================================


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPConfig(BaseModel):
    """Root MCP configuration."""

    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class ToolInfo(BaseModel):
    """Information about an available MCP tool."""

    name: str
    server: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Result from an MCP tool call."""

    success: bool
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured: dict[str, Any] | None = None
    error: str | None = None


# =============================================================================
# MCP Manager
# =============================================================================


class MCPManager:
    """Manages MCP server connections and tool execution.

    Usage:
        manager = MCPManager()
        tools = await manager.list_tools()
        result = await manager.call_tool("gmail", "search_emails", {"query": "from:client"})
    """

    def __init__(self, config_path: Path | None = None):
        """Initialize MCP manager.

        Args:
            config_path: Path to mcp.json config file. Defaults to ~/.config/pai/mcp.json.
        """
        self._config_path = config_path or (get_config_dir() / "mcp.json")
        self._config: MCPConfig | None = None
        self._tools_cache: dict[str, list[ToolInfo]] = {}

    def load_config(self) -> MCPConfig:
        """Load MCP configuration from file.

        Returns:
            MCPConfig with server definitions.
        """
        if self._config is not None:
            return self._config

        if not self._config_path.exists():
            self._config = MCPConfig()
            return self._config

        with open(self._config_path) as f:
            data = json.load(f)

        self._config = MCPConfig.model_validate(data)
        return self._config

    def save_config(self, config: MCPConfig) -> None:
        """Save MCP configuration to file.

        Args:
            config: Configuration to save.
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w") as f:
            json.dump(config.model_dump(), f, indent=2)
        self._config = config

    def get_server_names(self) -> list[str]:
        """Get list of configured server names.

        Returns:
            List of server names.
        """
        config = self.load_config()
        return list(config.servers.keys())

    def get_server_config(self, server_name: str) -> MCPServerConfig | None:
        """Get configuration for a specific server.

        Args:
            server_name: Name of the server.

        Returns:
            Server configuration or None if not found.
        """
        config = self.load_config()
        return config.servers.get(server_name)

    @asynccontextmanager
    async def connect(self, server_name: str):
        """Connect to an MCP server.

        Args:
            server_name: Name of the server to connect to.

        Yields:
            ClientSession connected to the server.

        Raises:
            ValueError: If server not found in config.
        """
        server_config = self.get_server_config(server_name)
        if not server_config:
            raise ValueError(f"MCP server not configured: {server_name}")

        # Expand environment variables and ~ in paths
        env = {
            k: os.path.expanduser(os.path.expandvars(v))
            for k, v in server_config.env.items()
        }

        # Merge with current environment
        full_env = {**os.environ, **env}

        server_params = StdioServerParameters(
            command=server_config.command,
            args=server_config.args,
            env=full_env,
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self, server_name: str | None = None) -> list[ToolInfo]:
        """List available tools from MCP servers.

        Args:
            server_name: Specific server to query, or None for all servers.

        Returns:
            List of available tools.
        """
        servers = [server_name] if server_name else self.get_server_names()
        all_tools: list[ToolInfo] = []

        for name in servers:
            # Check cache
            if name in self._tools_cache:
                all_tools.extend(self._tools_cache[name])
                continue

            try:
                async with self.connect(name) as session:
                    result = await session.list_tools()
                    server_tools = []

                    for tool in result.tools:
                        tool_info = ToolInfo(
                            name=tool.name,
                            server=name,
                            description=tool.description or "",
                            input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                        )
                        server_tools.append(tool_info)

                    self._tools_cache[name] = server_tools
                    all_tools.extend(server_tools)

            except Exception as e:
                # Log but don't fail - server might not be running
                print(f"[mcp] Warning: Could not connect to {name}: {e}")

        return all_tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Call a tool on an MCP server.

        Args:
            server_name: Name of the server.
            tool_name: Name of the tool to call.
            arguments: Arguments to pass to the tool.

        Returns:
            ToolResult with success status and content.
        """
        try:
            async with self.connect(server_name) as session:
                result = await session.call_tool(tool_name, arguments or {})

                # Extract content
                content_list = []
                for item in result.content:
                    if isinstance(item, types.TextContent):
                        content_list.append({"type": "text", "text": item.text})
                    elif isinstance(item, types.ImageContent):
                        content_list.append({
                            "type": "image",
                            "data": item.data,
                            "mime_type": item.mimeType,
                        })
                    elif isinstance(item, types.EmbeddedResource):
                        content_list.append({
                            "type": "resource",
                            "uri": str(item.resource.uri),
                        })

                return ToolResult(
                    success=not result.isError if hasattr(result, "isError") else True,
                    content=content_list,
                    structured=result.structuredContent if hasattr(result, "structuredContent") else None,
                    error=None,
                )

        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
            )

    def clear_cache(self, server_name: str | None = None) -> None:
        """Clear the tools cache.

        Args:
            server_name: Specific server to clear, or None for all.
        """
        if server_name:
            self._tools_cache.pop(server_name, None)
        else:
            self._tools_cache.clear()


# =============================================================================
# Tool Name Mapping
# =============================================================================


# Maps PAI action types to MCP server + tool names
ACTION_TO_MCP_TOOL: dict[str, tuple[str, str]] = {
    # Gmail actions -> gmail MCP server
    "email.label": ("gmail", "add_label"),
    "email.archive": ("gmail", "archive_email"),
    "email.send": ("gmail", "send_email"),
    # Outlook actions -> outlook MCP server
    "outlook.list_emails": ("outlook", "list_emails"),
    "outlook.reply": ("outlook", "reply_to_email"),
    "outlook.get_email": ("outlook", "get_email_details"),
    "outlook.mark_read": ("outlook", "mark_email_read"),
    "outlook.list_events": ("outlook", "list_calendar_events"),
    "outlook.get_event": ("outlook", "get_calendar_event_details"),
}


def get_mcp_tool_for_action(action_type: str) -> tuple[str, str] | None:
    """Get the MCP server and tool name for a PAI action type.

    Args:
        action_type: PAI action type (e.g., "email.label").

    Returns:
        Tuple of (server_name, tool_name) or None if not mapped.
    """
    return ACTION_TO_MCP_TOOL.get(action_type)


# =============================================================================
# Convenience Functions
# =============================================================================


_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Get the global MCP manager instance."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


async def call_mcp_tool(
    server: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> ToolResult:
    """Convenience function to call an MCP tool.

    Args:
        server: MCP server name.
        tool: Tool name.
        arguments: Tool arguments.

    Returns:
        ToolResult from the tool call.
    """
    manager = get_mcp_manager()
    return await manager.call_tool(server, tool, arguments)
