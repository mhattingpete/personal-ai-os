"""Execution engine for PAI automations.

Handles running automations, executing actions, and logging results.
All execution goes through MCP-based connectors.
"""

import re
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from pai.db import get_db
from pai.mcp import get_mcp_manager, get_mcp_tool_for_action
from pai.models import (
    Action,
    ActionResult,
    Automation,
    AutomationStatus,
    Execution,
    ExecutionError,
    ExecutionStatus,
    ResolvedVariable,
    TriggerEvent,
)


# =============================================================================
# MCP Action Executor
# =============================================================================


class MCPActionExecutor:
    """Executor that routes actions through MCP servers."""

    def __init__(self):
        self._manager = get_mcp_manager()

    def can_handle(self, action: Action) -> bool:
        """Check if this action can be handled via MCP."""
        if isinstance(action, dict):
            action_type = action.get("type", "")
        else:
            action_type = action.type

        # Check if we have an MCP mapping for this action type
        mcp_mapping = get_mcp_tool_for_action(action_type)
        if not mcp_mapping:
            return False

        # Check if the server is configured
        server_name, _ = mcp_mapping
        return self._manager.get_server_config(server_name) is not None

    async def execute(
        self,
        action: Action,
        variables: dict[str, Any],
        dry_run: bool = False,
    ) -> ActionResult:
        """Execute an action via MCP."""
        start_time = time.time()
        action_id = f"mcp_{uuid4().hex[:8]}"

        # Get action type
        if isinstance(action, dict):
            action_type = action.get("type", "")
            action_data = action
        else:
            action_type = action.type
            action_data = action.model_dump()

        # Resolve variables in action parameters
        action_data = self._resolve_templates(action_data, variables)

        # Get MCP mapping
        mcp_mapping = get_mcp_tool_for_action(action_type)
        if not mcp_mapping:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error=f"No MCP mapping for action type: {action_type}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        server_name, tool_name = mcp_mapping

        # Convert PAI action data to MCP tool arguments
        tool_args = self._convert_to_mcp_args(action_type, action_data)

        if dry_run:
            # Build output with resolved fields at top level
            output = {
                "dry_run": True,
                "would_execute": f"{server_name}.{tool_name}",
                "description": f"Would call MCP tool '{tool_name}' on server '{server_name}'",
                "arguments": tool_args,
            }
            # Include resolved values at top level for easy access
            output.update(tool_args)
            return ActionResult(
                action_id=action_id,
                status="success",
                output=output,
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Call MCP tool
        result = await self._manager.call_tool(server_name, tool_name, tool_args)

        if result.success:
            # Extract text content for output
            output = {"mcp_server": server_name, "mcp_tool": tool_name}
            for content in result.content:
                if content.get("type") == "text":
                    output["result"] = content.get("text")
            if result.structured:
                output["structured"] = result.structured

            return ActionResult(
                action_id=action_id,
                status="success",
                output=output,
                duration_ms=int((time.time() - start_time) * 1000),
            )
        else:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error=result.error or "MCP tool call failed",
                duration_ms=int((time.time() - start_time) * 1000),
            )

    def _convert_to_mcp_args(self, action_type: str, action_data: dict) -> dict[str, Any]:
        """Convert PAI action data to MCP tool arguments."""
        # Email actions
        if action_type == "email.label":
            return {
                "message_id": action_data.get("message_id"),
                "label": action_data.get("label"),
            }
        elif action_type == "email.archive":
            return {
                "message_id": action_data.get("message_id"),
            }
        elif action_type == "email.send":
            return {
                "to": action_data.get("to"),
                "subject": action_data.get("subject"),
                "body": action_data.get("body"),
            }

        # Default: pass through action data (minus type field)
        return {k: v for k, v in action_data.items() if k != "type"}

    def _resolve_templates(self, data: dict, variables: dict[str, Any]) -> dict:
        """Resolve ${variable} templates in action data."""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self._resolve_string(value, variables)
            elif isinstance(value, dict):
                result[key] = self._resolve_templates(value, variables)
            elif isinstance(value, list):
                result[key] = [
                    self._resolve_string(v, variables) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result

    def _resolve_string(self, template: str, variables: dict[str, Any]) -> str:
        """Resolve ${var.path} in a string."""
        pattern = r"\$\{([^}]+)\}"

        def replace(match):
            path = match.group(1)
            value = self._get_nested_value(variables, path)
            return str(value) if value is not None else match.group(0)

        return re.sub(pattern, replace, template)

    def _get_nested_value(self, data: dict, path: str) -> Any:
        """Get nested value from dict using dot notation."""
        parts = path.split(".")
        value = data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value


# =============================================================================
# Execution Engine
# =============================================================================


class ExecutionEngine:
    """Main execution engine for running automations via MCP.

    Usage:
        engine = ExecutionEngine()
        result = await engine.run(automation, trigger_event, dry_run=True)
    """

    def __init__(self):
        """Initialize execution engine."""
        self._executor = MCPActionExecutor()

    async def run(
        self,
        automation: Automation,
        trigger_event: TriggerEvent | None = None,
        dry_run: bool = False,
    ) -> Execution:
        """Run an automation.

        Args:
            automation: The automation to run.
            trigger_event: Event that triggered this execution (or None for manual).
            dry_run: If True, simulate but don't actually execute actions.

        Returns:
            Execution record with results.
        """
        # Create execution record
        execution = Execution(
            id=f"exec_{uuid4().hex[:12]}",
            automation_id=automation.id,
            automation_version=automation.version,
            triggered_at=datetime.now(),
            status=ExecutionStatus.RUNNING,
            trigger_event=trigger_event or TriggerEvent(type="manual"),
        )

        # Resolve variables
        variables = self._resolve_variables(automation, trigger_event)
        execution.variables = [
            ResolvedVariable(name=k, value=v) for k, v in variables.items()
        ]

        # Execute actions
        action_results = []
        failed = False

        for i, action in enumerate(automation.actions):
            if not self._executor.can_handle(action):
                action_type = action.type if hasattr(action, "type") else action.get("type")
                action_results.append(
                    ActionResult(
                        action_id=f"action_{i}",
                        status="failed",
                        error=f"No MCP server configured for action type: {action_type}",
                    )
                )
                failed = True
                continue

            result = await self._executor.execute(action, variables, dry_run=dry_run)
            action_results.append(result)

            if result.status == "failed":
                failed = True
                break

        # Update execution record
        execution.action_results = action_results
        execution.completed_at = datetime.now()

        if failed:
            execution.status = ExecutionStatus.FAILED
            for result in action_results:
                if result.error:
                    execution.error = ExecutionError(
                        message=result.error,
                        action_id=result.action_id,
                        recoverable=True,
                    )
                    break
        else:
            execution.status = ExecutionStatus.SUCCESS

        # Save execution to database (unless dry-run)
        if not dry_run:
            db = get_db()
            await db.save_execution(execution)

        return execution

    def _resolve_variables(
        self,
        automation: Automation,
        trigger_event: TriggerEvent | None,
    ) -> dict[str, Any]:
        """Resolve variables from automation definition and trigger event."""
        variables: dict[str, Any] = {}

        # Add trigger event data
        if trigger_event:
            variables["trigger"] = trigger_event.data

        # Add automation variables
        for var in automation.variables:
            variables[var.name] = None

        return variables


# =============================================================================
# Convenience Functions
# =============================================================================


async def run_automation(
    automation_id: str,
    trigger_event: TriggerEvent | None = None,
    dry_run: bool = False,
) -> Execution:
    """Run an automation by ID.

    Args:
        automation_id: ID of the automation to run.
        trigger_event: Optional trigger event data.
        dry_run: If True, simulate but don't execute.

    Returns:
        Execution record.

    Raises:
        ValueError: If automation not found.
    """
    db = get_db()
    await db.initialize()

    try:
        automation = await db.get_automation(automation_id)
        if not automation:
            raise ValueError(f"Automation not found: {automation_id}")

        engine = ExecutionEngine()
        return await engine.run(automation, trigger_event, dry_run=dry_run)
    finally:
        await db.close()


async def activate_automation(automation_id: str) -> Automation:
    """Activate an automation (set status to active).

    Args:
        automation_id: ID of the automation to activate.

    Returns:
        Updated automation.

    Raises:
        ValueError: If automation not found.
    """
    db = get_db()
    await db.initialize()

    try:
        automation = await db.get_automation(automation_id)
        if not automation:
            raise ValueError(f"Automation not found: {automation_id}")

        automation.status = AutomationStatus.ACTIVE
        automation.updated_at = datetime.now()
        await db.save_automation(automation)

        return automation
    finally:
        await db.close()


async def pause_automation(automation_id: str) -> Automation:
    """Pause an automation (set status to paused).

    Args:
        automation_id: ID of the automation to pause.

    Returns:
        Updated automation.

    Raises:
        ValueError: If automation not found.
    """
    db = get_db()
    await db.initialize()

    try:
        automation = await db.get_automation(automation_id)
        if not automation:
            raise ValueError(f"Automation not found: {automation_id}")

        automation.status = AutomationStatus.PAUSED
        automation.updated_at = datetime.now()
        await db.save_automation(automation)

        return automation
    finally:
        await db.close()
