"""Execution engine for PAI automations.

Handles running automations, executing actions, and logging results.
All execution goes through this module.
"""

import re
import time
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from pai.db import get_db
from pai.gmail import get_gmail_client
from pai.models import (
    Action,
    ActionResult,
    Automation,
    AutomationStatus,
    EmailAction,
    Execution,
    ExecutionError,
    ExecutionStatus,
    ResolvedVariable,
    TriggerEvent,
)


# =============================================================================
# Action Executor Protocol
# =============================================================================


class ActionExecutor(Protocol):
    """Protocol for action executors.

    Each action type (email, file, spreadsheet) has its own executor.
    """

    async def execute(
        self,
        action: Action,
        variables: dict[str, Any],
        dry_run: bool = False,
    ) -> ActionResult:
        """Execute an action.

        Args:
            action: The action to execute.
            variables: Resolved variables for template substitution.
            dry_run: If True, simulate but don't actually execute.

        Returns:
            ActionResult with status and output.
        """
        ...

    def can_handle(self, action: Action) -> bool:
        """Check if this executor can handle the given action."""
        ...


# =============================================================================
# Email Action Executor
# =============================================================================


class EmailActionExecutor:
    """Executor for email actions (label, archive, etc.)."""

    def __init__(self):
        self._client = None
        self._labels_cache: dict[str, str] = {}  # name -> id

    def can_handle(self, action: Action) -> bool:
        """Check if this is an email action."""
        if isinstance(action, EmailAction):
            return True
        if isinstance(action, dict):
            return action.get("type", "").startswith("email.")
        return False

    async def execute(
        self,
        action: Action,
        variables: dict[str, Any],
        dry_run: bool = False,
    ) -> ActionResult:
        """Execute an email action."""
        start_time = time.time()
        action_id = f"email_{uuid4().hex[:8]}"

        # Handle dict or EmailAction
        if isinstance(action, dict):
            action_type = action.get("type", "")
            action_data = action
        else:
            action_type = action.type
            action_data = action.model_dump()

        # Resolve variables in action parameters
        action_data = self._resolve_templates(action_data, variables)

        try:
            if dry_run:
                return self._dry_run_result(action_id, action_type, action_data, start_time)

            # Get Gmail client
            if self._client is None:
                self._client = get_gmail_client()

            # Execute based on action type
            if action_type == "email.label":
                return await self._execute_label(action_id, action_data, start_time)
            elif action_type == "email.archive":
                return await self._execute_archive(action_id, action_data, start_time)
            else:
                return ActionResult(
                    action_id=action_id,
                    status="failed",
                    error=f"Unknown email action type: {action_type}",
                    duration_ms=int((time.time() - start_time) * 1000),
                )

        except Exception as e:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    def _dry_run_result(
        self,
        action_id: str,
        action_type: str,
        action_data: dict,
        start_time: float,
    ) -> ActionResult:
        """Generate a dry-run result showing what would happen."""
        output = {"dry_run": True, "would_execute": action_type}

        if action_type == "email.label":
            output["message_id"] = action_data.get("message_id", "?")
            output["label"] = action_data.get("label", "?")
            output["description"] = f"Would add label '{action_data.get('label')}' to message"
        elif action_type == "email.archive":
            output["message_id"] = action_data.get("message_id", "?")
            output["description"] = "Would archive message (remove from INBOX)"

        return ActionResult(
            action_id=action_id,
            status="success",
            output=output,
            duration_ms=int((time.time() - start_time) * 1000),
        )

    async def _execute_label(
        self,
        action_id: str,
        action_data: dict,
        start_time: float,
    ) -> ActionResult:
        """Add a label to an email."""
        message_id = action_data.get("message_id")
        label_name = action_data.get("label")

        if not message_id or not label_name:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error="Missing message_id or label",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Check if label exists before getting/creating
        labels_before = set(self._labels_cache.keys())
        if not labels_before:
            # Populate cache
            labels = await self._client.list_labels()
            for label in labels:
                self._labels_cache[label["name"]] = label["id"]
            labels_before = set(self._labels_cache.keys())

        # Get or create label ID
        label_id = await self._get_label_id(label_name)
        if not label_id:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error=f"Failed to get or create label '{label_name}'",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        label_created = label_name not in labels_before

        # Add label
        success = await self._client.add_label(message_id, label_id)

        return ActionResult(
            action_id=action_id,
            status="success" if success else "failed",
            output={
                "message_id": message_id,
                "label_id": label_id,
                "label_name": label_name,
                "label_created": label_created,
            },
            error=None if success else "Failed to add label",
            duration_ms=int((time.time() - start_time) * 1000),
        )

    async def _execute_archive(
        self,
        action_id: str,
        action_data: dict,
        start_time: float,
    ) -> ActionResult:
        """Archive an email (remove from INBOX)."""
        message_id = action_data.get("message_id")

        if not message_id:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error="Missing message_id",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Archive
        success = await self._client.archive(message_id)

        return ActionResult(
            action_id=action_id,
            status="success" if success else "failed",
            output={"message_id": message_id, "archived": success},
            error=None if success else "Failed to archive message",
            duration_ms=int((time.time() - start_time) * 1000),
        )

    async def _get_label_id(self, label_name: str, auto_create: bool = True) -> str | None:
        """Get label ID from name, using cache. Creates label if not found.

        Args:
            label_name: Name of the label.
            auto_create: If True, create the label if it doesn't exist.

        Returns:
            Label ID or None if not found and auto_create is False.
        """
        # Check cache
        if label_name in self._labels_cache:
            return self._labels_cache[label_name]

        # Fetch all labels
        labels = await self._client.list_labels()
        for label in labels:
            self._labels_cache[label["name"]] = label["id"]

        # Return if found
        if label_name in self._labels_cache:
            return self._labels_cache[label_name]

        # Auto-create if not found
        if auto_create:
            new_label = await self._client.create_label(label_name)
            self._labels_cache[new_label["name"]] = new_label["id"]
            return new_label["id"]

        return None

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
    """Main execution engine for running automations.

    Usage:
        engine = ExecutionEngine()
        result = await engine.run(automation, trigger_event, dry_run=True)
    """

    def __init__(self):
        self._executors: list[ActionExecutor] = [
            EmailActionExecutor(),
        ]

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
            executor = self._get_executor(action)
            if not executor:
                action_results.append(
                    ActionResult(
                        action_id=f"action_{i}",
                        status="failed",
                        error=f"No executor for action type: {action.type if hasattr(action, 'type') else action.get('type')}",
                    )
                )
                failed = True
                continue

            result = await executor.execute(action, variables, dry_run=dry_run)
            action_results.append(result)

            if result.status == "failed":
                failed = True
                # Check error handling - for now, stop on first failure
                break

        # Update execution record
        execution.action_results = action_results
        execution.completed_at = datetime.now()

        if failed:
            execution.status = ExecutionStatus.FAILED
            # Find first error
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

    def _get_executor(self, action: Action) -> ActionExecutor | None:
        """Find an executor that can handle this action."""
        for executor in self._executors:
            if executor.can_handle(action):
                return executor
        return None

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
            # Simple resolution - in a full implementation, this would
            # resolve from various sources based on var.resolved_from
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
