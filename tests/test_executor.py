"""Tests for the execution engine."""

import pytest
from unittest.mock import MagicMock, patch

from pai.executor import (
    MCPActionExecutor,
    ExecutionEngine,
)
from pai.models import (
    Action,
    ActionResult,
    Automation,
    AutomationStatus,
    EmailAction,
    EmailTrigger,
    EmailCondition,
    ExecutionStatus,
    GitHubPRTrigger,
    GitHubReviewAction,
    ManualTrigger,
    TriggerEvent,
)


class TestMCPActionExecutor:
    """Tests for MCP action executor."""

    @pytest.fixture
    def mock_mcp_manager(self):
        """Create a mock MCP manager with gmail configured."""
        manager = MagicMock()
        manager.get_server_config.return_value = {"command": "uv", "args": ["run", "pai-gmail-mcp"]}
        return manager

    @pytest.fixture
    def executor(self, mock_mcp_manager):
        """Create an executor instance with mocked MCP manager."""
        with patch("pai.executor.get_mcp_manager", return_value=mock_mcp_manager):
            return MCPActionExecutor()

    def test_can_handle_email_action_dict(self, executor):
        """Test that executor handles email action dicts when server configured."""
        action = {"type": "email.label", "label": "Test"}
        assert executor.can_handle(action) is True

    def test_can_handle_email_action_model(self, executor):
        """Test that executor handles EmailAction models."""
        action = EmailAction(
            type="email.label",
            connector="gmail",
            label="Test",
        )
        assert executor.can_handle(action) is True

    def test_cannot_handle_unmapped_action(self, executor):
        """Test that executor rejects actions without MCP mapping."""
        action = {"type": "calendar.create", "title": "Meeting"}
        assert executor.can_handle(action) is False

    def test_cannot_handle_when_server_not_configured(self, mock_mcp_manager):
        """Test that executor rejects when MCP server not configured."""
        mock_mcp_manager.get_server_config.return_value = None
        with patch("pai.executor.get_mcp_manager", return_value=mock_mcp_manager):
            executor = MCPActionExecutor()
        action = {"type": "email.label", "label": "Test"}
        assert executor.can_handle(action) is False

    @pytest.mark.asyncio
    async def test_dry_run_label_action(self, executor):
        """Test dry run of label action."""
        action = {
            "type": "email.label",
            "message_id": "msg_123",
            "label": "Important",
        }

        result = await executor.execute(action, {}, dry_run=True)

        assert result.status == "success"
        assert result.output["dry_run"] is True
        assert "gmail.add_label" in result.output["would_execute"]
        assert result.output["message_id"] == "msg_123"
        assert result.output["label"] == "Important"

    @pytest.mark.asyncio
    async def test_dry_run_archive_action(self, executor):
        """Test dry run of archive action."""
        action = {
            "type": "email.archive",
            "message_id": "msg_456",
        }

        result = await executor.execute(action, {}, dry_run=True)

        assert result.status == "success"
        assert result.output["dry_run"] is True
        assert "gmail.archive_email" in result.output["would_execute"]
        assert result.output["message_id"] == "msg_456"

    @pytest.mark.asyncio
    async def test_dry_run_with_variable_resolution(self, executor):
        """Test variable resolution in dry run."""
        action = {
            "type": "email.label",
            "message_id": "${trigger.email.id}",
            "label": "${trigger.email.label}",
        }

        variables = {
            "trigger": {
                "email": {
                    "id": "resolved_msg_id",
                    "label": "ResolvedLabel",
                }
            }
        }

        result = await executor.execute(action, variables, dry_run=True)

        assert result.status == "success"
        assert result.output["message_id"] == "resolved_msg_id"
        assert result.output["label"] == "ResolvedLabel"

    def test_resolve_string_simple(self, executor):
        """Test simple variable resolution."""
        template = "Hello ${name}"
        variables = {"name": "World"}

        result = executor._resolve_string(template, variables)
        assert result == "Hello World"

    def test_resolve_string_nested(self, executor):
        """Test nested variable resolution."""
        template = "Email: ${trigger.email.subject}"
        variables = {"trigger": {"email": {"subject": "Test Subject"}}}

        result = executor._resolve_string(template, variables)
        assert result == "Email: Test Subject"

    def test_resolve_string_missing_variable(self, executor):
        """Test that missing variables are kept as-is."""
        template = "Value: ${missing.var}"
        variables = {}

        result = executor._resolve_string(template, variables)
        assert result == "Value: ${missing.var}"


class TestExecutionEngine:
    """Tests for the execution engine."""

    @pytest.fixture
    def mock_mcp_manager(self):
        """Create a mock MCP manager with gmail configured."""
        manager = MagicMock()
        manager.get_server_config.return_value = {"command": "uv", "args": ["run", "pai-gmail-mcp"]}
        return manager

    @pytest.fixture
    def engine(self, mock_mcp_manager):
        """Create an engine instance with mocked MCP manager."""
        with patch("pai.executor.get_mcp_manager", return_value=mock_mcp_manager):
            return ExecutionEngine()

    @pytest.fixture
    def sample_automation(self):
        """Create a sample automation."""
        return Automation(
            id="test_auto_001",
            name="Test Automation",
            description="A test automation",
            status=AutomationStatus.ACTIVE,
            trigger=ManualTrigger(),
            actions=[
                EmailAction(
                    type="email.label",
                    connector="gmail",
                    label="TestLabel",
                    message_id="msg_123",
                ),
            ],
        )

    @pytest.fixture
    def email_trigger_automation(self):
        """Create an automation with email trigger."""
        return Automation(
            id="test_email_001",
            name="Email Label Automation",
            description="Labels emails from specific sender",
            status=AutomationStatus.ACTIVE,
            trigger=EmailTrigger(
                account="me@gmail.com",
                conditions=[
                    EmailCondition(
                        field="from",
                        operator="contains",
                        value="@acme.com",
                    )
                ],
            ),
            actions=[
                EmailAction(
                    type="email.label",
                    connector="gmail",
                    label="Client",
                    message_id="${trigger.email.id}",
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_dry_run_execution(self, engine, sample_automation):
        """Test dry run execution."""
        trigger_event = TriggerEvent(type="manual")

        execution = await engine.run(sample_automation, trigger_event, dry_run=True)

        assert execution.automation_id == sample_automation.id
        assert execution.status == ExecutionStatus.SUCCESS
        assert len(execution.action_results) == 1
        assert execution.action_results[0].output["dry_run"] is True

    @pytest.mark.asyncio
    async def test_execution_with_trigger_variables(self, engine, email_trigger_automation):
        """Test that trigger event data becomes variables."""
        trigger_event = TriggerEvent(
            type="email",
            data={
                "email": {
                    "id": "email_789",
                    "subject": "Invoice from ACME",
                    "from": "billing@acme.com",
                }
            },
        )

        execution = await engine.run(
            email_trigger_automation, trigger_event, dry_run=True
        )

        assert execution.status == ExecutionStatus.SUCCESS
        # Variable should have been resolved
        assert execution.action_results[0].output["message_id"] == "email_789"

    @pytest.mark.asyncio
    async def test_execution_without_trigger_event(self, engine, sample_automation):
        """Test execution with no trigger event (manual)."""
        execution = await engine.run(sample_automation, dry_run=True)

        assert execution.trigger_event.type == "manual"
        assert execution.status == ExecutionStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_execution_records_completion_time(self, engine, sample_automation):
        """Test that execution records completion time."""
        execution = await engine.run(sample_automation, dry_run=True)

        assert execution.triggered_at is not None
        assert execution.completed_at is not None
        assert execution.completed_at >= execution.triggered_at

    @pytest.mark.asyncio
    async def test_execution_fails_when_no_mcp_server(self, sample_automation):
        """Test that execution fails when MCP server not configured."""
        mock_manager = MagicMock()
        mock_manager.get_server_config.return_value = None

        with patch("pai.executor.get_mcp_manager", return_value=mock_manager):
            engine = ExecutionEngine()
            execution = await engine.run(sample_automation, dry_run=True)

        assert execution.status == ExecutionStatus.FAILED
        assert "No MCP server configured" in execution.action_results[0].error


class TestVariableResolution:
    """Tests for variable resolution in execution context."""

    @pytest.fixture
    def engine(self):
        with patch("pai.executor.get_mcp_manager"):
            return ExecutionEngine()

    def test_resolve_variables_from_trigger_event(self, engine):
        """Test that trigger event data is available as variables."""
        automation = Automation(
            id="test",
            name="Test",
            trigger=ManualTrigger(),
            actions=[],
        )

        trigger_event = TriggerEvent(
            type="email",
            data={
                "email": {"id": "123", "subject": "Test"},
                "sender": "test@example.com",
            },
        )

        variables = engine._resolve_variables(automation, trigger_event)

        assert "trigger" in variables
        assert variables["trigger"]["email"]["id"] == "123"
        assert variables["trigger"]["sender"] == "test@example.com"

    def test_resolve_variables_without_trigger(self, engine):
        """Test variable resolution with no trigger event."""
        automation = Automation(
            id="test",
            name="Test",
            trigger=ManualTrigger(),
            actions=[],
        )

        variables = engine._resolve_variables(automation, None)

        # Should still work, just with empty trigger data
        assert isinstance(variables, dict)


class TestActionResult:
    """Tests for action result model."""

    def test_success_result(self):
        """Test creating a success result."""
        result = ActionResult(
            action_id="action_1",
            status="success",
            output={"key": "value"},
        )
        assert result.status == "success"
        assert result.error is None

    def test_failed_result(self):
        """Test creating a failed result."""
        result = ActionResult(
            action_id="action_2",
            status="failed",
            error="Something went wrong",
        )
        assert result.status == "failed"
        assert result.error == "Something went wrong"

    def test_result_with_duration(self):
        """Test result with duration tracking."""
        result = ActionResult(
            action_id="action_3",
            status="success",
            duration_ms=150,
        )
        assert result.duration_ms == 150


class TestGitHubReviewExecution:
    """Tests for GitHub review implementation action."""

    @pytest.fixture
    def mock_mcp_manager(self):
        """Create a mock MCP manager with github configured."""
        manager = MagicMock()
        manager.get_server_config.return_value = {"command": "uv", "args": ["run", "pai-github-mcp"]}
        return manager

    @pytest.fixture
    def executor(self, mock_mcp_manager):
        """Create an executor instance with mocked MCP manager."""
        with patch("pai.executor.get_mcp_manager", return_value=mock_mcp_manager):
            return MCPActionExecutor()

    @pytest.mark.asyncio
    async def test_dry_run_github_review_action(self, executor):
        """Test dry run of github.implement_review action."""
        action_data = {
            "type": "github.implement_review",
            "repo": "owner/test-repo",
            "pr_number": 123,
        }

        variables = {
            "trigger": {
                "repo": "owner/test-repo",
                "pr_number": 123,
                "branch": "feature-branch",
                "prompt": "# Review\n\nPlease fix the bug.",
            }
        }

        result = await executor._execute_github_review(action_data, variables, dry_run=True)

        assert result.status == "success"
        assert result.output["dry_run"] is True
        assert result.output["repo"] == "owner/test-repo"
        assert result.output["pr_number"] == 123
        assert result.output["branch"] == "feature-branch"
        assert "would_write_to" in result.output
        assert "owner_test-repo_123.md" in result.output["would_write_to"]

    @pytest.mark.asyncio
    async def test_github_review_uses_trigger_data(self, executor):
        """Test that action uses trigger data when not specified."""
        action_data = {
            "type": "github.implement_review",
            # repo and pr_number not specified - should come from trigger
        }

        variables = {
            "trigger": {
                "repo": "from/trigger",
                "pr_number": 456,
                "branch": "pr-branch",
                "prompt": "# Fix this\n\nDetails here.",
            }
        }

        result = await executor._execute_github_review(action_data, variables, dry_run=True)

        assert result.status == "success"
        assert result.output["repo"] == "from/trigger"
        assert result.output["pr_number"] == 456

    @pytest.mark.asyncio
    async def test_github_review_fails_without_repo(self, executor):
        """Test that action fails when repo is missing."""
        action_data = {
            "type": "github.implement_review",
        }

        variables = {}  # No trigger data

        result = await executor._execute_github_review(action_data, variables, dry_run=True)

        assert result.status == "failed"
        assert "Missing repo or pr_number" in result.error

    @pytest.mark.asyncio
    async def test_github_review_includes_additional_instructions(self, executor):
        """Test that additional instructions are included in output."""
        action_data = {
            "type": "github.implement_review",
            "repo": "owner/repo",
            "pr_number": 1,
            "additional_instructions": "Run tests before committing",
        }

        variables = {
            "trigger": {
                "prompt": "# Review\n\nFix the issue.",
                "branch": "main",
            }
        }

        result = await executor._execute_github_review(action_data, variables, dry_run=True)

        assert result.status == "success"
        # Additional instructions should be in the prompt preview
        assert "Run tests before committing" in result.output["prompt_preview"]

    @pytest.mark.asyncio
    async def test_github_review_action_routing(self, executor):
        """Test that github.implement_review routes to specialized handler."""
        action = {
            "type": "github.implement_review",
            "repo": "test/repo",
            "pr_number": 99,
        }

        variables = {
            "trigger": {
                "prompt": "Test prompt",
                "branch": "test-branch",
            }
        }

        # Execute through the main execute method
        result = await executor.execute(action, variables, dry_run=True)

        # Should have been handled by _execute_github_review
        assert result.status == "success"
        assert result.output["repo"] == "test/repo"
        assert result.output["pr_number"] == 99


class TestGitHubAutomationExecution:
    """Tests for full automation execution with GitHub triggers."""

    @pytest.fixture
    def mock_mcp_manager(self):
        """Create a mock MCP manager with github configured."""
        manager = MagicMock()
        manager.get_server_config.return_value = {"command": "uv", "args": ["run", "pai-github-mcp"]}
        return manager

    @pytest.fixture
    def engine(self, mock_mcp_manager):
        """Create an engine instance with mocked MCP manager."""
        with patch("pai.executor.get_mcp_manager", return_value=mock_mcp_manager):
            return ExecutionEngine()

    @pytest.fixture
    def github_automation(self):
        """Create a sample automation with GitHub trigger."""
        return Automation(
            id="test_github_001",
            name="Implement Reviews",
            description="Implement PR review feedback",
            status=AutomationStatus.ACTIVE,
            trigger=GitHubPRTrigger(account="testuser"),
            actions=[
                GitHubReviewAction(
                    additional_instructions="Focus on the main issues",
                )
            ],
        )

    @pytest.mark.asyncio
    async def test_execution_with_github_trigger(self, engine, github_automation):
        """Test execution with GitHub trigger event data."""
        trigger_event = TriggerEvent(
            type="github_pr",
            data={
                "repo": "testuser/my-project",
                "pr_number": 42,
                "branch": "feature-xyz",
                "prompt": "# Review Feedback\n\nPlease address the comments.",
                "review": {
                    "author": "reviewer",
                    "state": "changes_requested",
                },
            },
        )

        execution = await engine.run(github_automation, trigger_event, dry_run=True)

        assert execution.status == ExecutionStatus.SUCCESS
        assert len(execution.action_results) == 1
        assert execution.action_results[0].output["dry_run"] is True
        assert execution.action_results[0].output["repo"] == "testuser/my-project"
        assert execution.action_results[0].output["pr_number"] == 42
