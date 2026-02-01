"""Execution engine for PAI automations.

Handles running automations, executing actions, and logging results.
All execution goes through MCP-based connectors.
"""

import re
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from pai.db import get_db
from pai.llm import Message, get_provider
from pai.mcp import get_mcp_manager, get_mcp_tool_for_action
from pai.models import (
    Action,
    ActionResult,
    Automation,
    AutomationStatus,
    EmailClassifyAction,
    Execution,
    ExecutionError,
    ExecutionStatus,
    ResolvedVariable,
    TriggerEvent,
)


# =============================================================================
# Classification Models
# =============================================================================


class EmailClassification(BaseModel):
    """LLM classification result for email."""

    category: str  # One of the provided categories
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str  # Brief explanation for the classification


def build_classification_prompt(
    email: dict[str, Any],
    categories: list[str],
) -> str:
    """Build the prompt for email classification.

    Args:
        email: Email data with 'from', 'subject', 'body' fields.
        categories: List of category names to classify into.

    Returns:
        Prompt string for the LLM.
    """
    sender = email.get("from", "unknown")
    subject = email.get("subject", "(no subject)")
    body = email.get("body", "")[:1000]  # Truncate to avoid token limits

    categories_str = ", ".join(categories)

    return f"""Classify this email into exactly one category: {categories_str}

From: {sender}
Subject: {subject}
Body:
{body}

Based on the content, determine if this email requires the recipient to take action (reply, complete a task, make a decision) or is purely informational."""


# =============================================================================
# MCP Action Executor
# =============================================================================


class MCPActionExecutor:
    """Executor that routes actions through MCP servers."""

    def __init__(self, provider_name: str | None = None):
        self._manager = get_mcp_manager()
        self._provider_name = provider_name

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

        # Route email.classify to specialized handler
        if action_type == "email.classify":
            classify_action = EmailClassifyAction.model_validate(action_data)
            return await self._execute_classify(classify_action, variables, dry_run)

        # Route github.implement_review to specialized handler
        if action_type == "github.implement_review":
            return await self._execute_github_review(action_data, variables, dry_run)

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

    async def _execute_classify(
        self,
        action: EmailClassifyAction,
        variables: dict[str, Any],
        dry_run: bool = False,
    ) -> ActionResult:
        """Execute email classification action.

        1. Fetch email content via MCP gmail.get_email
        2. Build classification prompt
        3. Call LLM with complete_structured() -> EmailClassification
        4. Apply label via MCP gmail.add_label
        5. Return result with classification details
        """
        start_time = time.time()
        action_id = f"classify_{uuid4().hex[:8]}"

        # Get message ID from action or trigger data
        message_id = action.message_id
        if not message_id:
            message_id = self._get_nested_value(variables, "trigger.email.id")
        if not message_id:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error="No message_id provided and none found in trigger data",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Step 1: Fetch email content via MCP
        email_result = await self._manager.call_tool(
            "gmail", "get_email", {"message_id": message_id}
        )
        if not email_result.success:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error=f"Failed to fetch email: {email_result.error}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Parse email data from result
        email_data = {}
        for content in email_result.content:
            if content.get("type") == "text":
                import json
                try:
                    email_data = json.loads(content.get("text", "{}"))
                except json.JSONDecodeError:
                    email_data = {"body": content.get("text", "")}

        # Step 2: Build classification prompt
        prompt = build_classification_prompt(email_data, action.categories)
        if prompt is None:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error="build_classification_prompt() not implemented - this is your contribution point!",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        if dry_run:
            return ActionResult(
                action_id=action_id,
                status="success",
                output={
                    "dry_run": True,
                    "would_classify": True,
                    "message_id": message_id,
                    "categories": action.categories,
                    "email_subject": email_data.get("subject", ""),
                    "prompt_preview": prompt[:200] + "..." if len(prompt) > 200 else prompt,
                },
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Step 3: Call LLM for classification
        llm = get_provider(self._provider_name)
        classification = await llm.complete_structured(
            messages=[Message(role="user", content=prompt)],
            schema=EmailClassification,
            system="You are an email classification assistant. Classify the email into exactly one of the provided categories.",
            temperature=0.0,
        )

        # Validate category
        if classification.category not in action.categories:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error=f"LLM returned invalid category '{classification.category}'. Expected one of: {action.categories}",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Step 4: Apply label via MCP
        label = action.category_labels.get(classification.category)
        if label:
            label_result = await self._manager.call_tool(
                "gmail", "add_label", {"message_id": message_id, "label": label}
            )
            if not label_result.success:
                return ActionResult(
                    action_id=action_id,
                    status="failed",
                    error=f"Failed to apply label: {label_result.error}",
                    output={
                        "classification": classification.model_dump(),
                        "label_attempted": label,
                    },
                    duration_ms=int((time.time() - start_time) * 1000),
                )

        # Step 5: Return success with classification details
        return ActionResult(
            action_id=action_id,
            status="success",
            output={
                "message_id": message_id,
                "classification": classification.model_dump(),
                "label_applied": label,
                "email_subject": email_data.get("subject", ""),
            },
            duration_ms=int((time.time() - start_time) * 1000),
        )

    async def _execute_github_review(
        self,
        action_data: dict[str, Any],
        variables: dict[str, Any],
        dry_run: bool = False,
    ) -> ActionResult:
        """Execute GitHub review implementation action.

        This action prepares context for Claude Code to implement PR review feedback.
        It uses the bash automation feature (when available) to launch Claude Code
        with the formatted prompt.

        Until bash automation is implemented, this:
        1. Fetches PR review context via MCP
        2. Writes prompt to a file
        3. Returns instructions for the user to run Claude Code manually

        Once bash automation is available, it will:
        1. Fetch PR review context via MCP
        2. Execute a bash script that launches Claude Code with the prompt
        """
        from pathlib import Path

        start_time = time.time()
        action_id = f"github_review_{uuid4().hex[:8]}"

        # Get repo and PR number from action or trigger data
        repo = action_data.get("repo")
        pr_number = action_data.get("pr_number")

        if not repo:
            repo = self._get_nested_value(variables, "trigger.repo")
        if not pr_number:
            pr_number = self._get_nested_value(variables, "trigger.pr_number")

        if not repo or not pr_number:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error="Missing repo or pr_number - provide in action or trigger data",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Get formatted prompt from trigger data (already set by watcher)
        prompt = self._get_nested_value(variables, "trigger.prompt")
        branch = self._get_nested_value(variables, "trigger.branch")

        # If no prompt in trigger, fetch it via MCP
        if not prompt:
            format_result = await self._manager.call_tool(
                "github", "format_review_for_claude",
                {"repo": repo, "pr_number": pr_number},
            )
            if format_result.success and format_result.structured:
                prompt = format_result.structured.get("prompt", "")
                branch = format_result.structured.get("branch", "")

        if not prompt:
            return ActionResult(
                action_id=action_id,
                status="failed",
                error="Failed to get PR review context",
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Add any custom instructions
        additional = action_data.get("additional_instructions")
        if additional:
            prompt += f"\n\n## Additional Instructions\n{additional}"

        # Write prompt to a task file
        config_dir = Path.home() / ".config" / "pai" / "pr-tasks"
        config_dir.mkdir(parents=True, exist_ok=True)
        task_file = config_dir / f"{repo.replace('/', '_')}_{pr_number}.md"

        if dry_run:
            return ActionResult(
                action_id=action_id,
                status="success",
                output={
                    "dry_run": True,
                    "repo": repo,
                    "pr_number": pr_number,
                    "branch": branch,
                    "would_write_to": str(task_file),
                    "prompt_preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
                },
                duration_ms=int((time.time() - start_time) * 1000),
            )

        # Write the task file
        task_file.write_text(prompt)

        # Try to find the local repo path
        local_repo = action_data.get("local_repo_path")
        if not local_repo:
            # Try common locations
            repo_name = repo.split("/")[-1]
            search_paths = [
                Path.home() / "Documents" / "Repos" / repo_name,
                Path.home() / "repos" / repo_name,
                Path.home() / "code" / repo_name,
                Path.home() / repo_name,
                Path.cwd() / repo_name,
            ]
            for p in search_paths:
                if p.exists() and (p / ".git").exists():
                    local_repo = str(p)
                    break

        # Build the Claude Code command
        # TODO: Once bash automation is implemented, this will use BashAction
        # to run a sandboxed script that:
        # 1. cd to local_repo
        # 2. git checkout branch
        # 3. claude --print "$(cat task_file)"
        claude_cmd = f"cd {local_repo} && git checkout {branch} && claude --print \"$(cat {task_file})\""

        return ActionResult(
            action_id=action_id,
            status="success",
            output={
                "repo": repo,
                "pr_number": pr_number,
                "branch": branch,
                "task_file": str(task_file),
                "local_repo": local_repo,
                "claude_command": claude_cmd,
                "message": (
                    f"PR review context saved to {task_file}.\n"
                    f"To implement the changes, run:\n\n"
                    f"  {claude_cmd}\n\n"
                    "Or manually: cd to repo, checkout branch, then run claude with the task file."
                ),
            },
            duration_ms=int((time.time() - start_time) * 1000),
        )


# =============================================================================
# Execution Engine
# =============================================================================


class ExecutionEngine:
    """Main execution engine for running automations via MCP.

    Usage:
        engine = ExecutionEngine()
        result = await engine.run(automation, trigger_event, dry_run=True)
    """

    def __init__(self, provider_name: str | None = None):
        """Initialize execution engine.

        Args:
            provider_name: LLM provider name ("claude" or "local"). Defaults to config.
        """
        self._executor = MCPActionExecutor(provider_name=provider_name)

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
    provider_name: str | None = None,
) -> Execution:
    """Run an automation by ID.

    Args:
        automation_id: ID of the automation to run.
        trigger_event: Optional trigger event data.
        dry_run: If True, simulate but don't execute.
        provider_name: LLM provider name ("claude" or "local"). Defaults to config.

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

        engine = ExecutionEngine(provider_name=provider_name)
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
