"""Tests for the execution engine."""

import pytest
from datetime import datetime

from pai.executor import (
    EmailActionExecutor,
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
    ManualTrigger,
    TriggerEvent,
)


class TestEmailActionExecutor:
    """Tests for email action executor."""

    @pytest.fixture
    def executor(self):
        """Create an executor instance."""
        return EmailActionExecutor()

    def test_can_handle_email_action_dict(self, executor):
        """Test that executor handles email action dicts."""
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

    def test_cannot_handle_file_action(self, executor):
        """Test that executor rejects non-email actions."""
        action = {"type": "file.move", "path": "/tmp"}
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
        assert result.output["would_execute"] == "email.label"
        assert "Important" in result.output["description"]

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
        assert "archive" in result.output["description"].lower()

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
    def engine(self):
        """Create an engine instance."""
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


class TestVariableResolution:
    """Tests for variable resolution in execution context."""

    @pytest.fixture
    def engine(self):
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
