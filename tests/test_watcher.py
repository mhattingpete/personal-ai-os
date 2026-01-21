"""Tests for the trigger watcher module."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pai.gmail import Attachment, Email, EmailAddress
from pai.models import (
    Automation,
    AutomationStatus,
    EmailAction,
    EmailCondition,
    EmailTrigger,
)
from pai.watcher import EmailWatcher, TriggerMatcher


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_email():
    """Create a sample email for testing."""
    return Email(
        id="msg_123",
        thread_id="thread_123",
        subject="Invoice #1234 from ACME Corp",
        **{"from": EmailAddress(name="John Doe", email="john@acme.com", domain="acme.com")},
        to=[EmailAddress(name="Me", email="me@example.com", domain="example.com")],
        cc=[],
        date=datetime.now(),
        snippet="Please find attached invoice for services rendered.",
        body_text="Dear Customer,\n\nPlease find attached invoice #1234.\n\nBest regards,\nJohn",
        labels=["INBOX", "UNREAD"],
        attachments=[
            Attachment(id="att_1", filename="invoice_1234.pdf", mime_type="application/pdf", size=12345)
        ],
    )


@pytest.fixture
def sample_automation():
    """Create a sample automation with email trigger."""
    return Automation(
        id="auto_123",
        name="Label ACME emails",
        description="Label emails from ACME as Client",
        status=AutomationStatus.ACTIVE,
        trigger=EmailTrigger(
            account="me@example.com",
            conditions=[
                EmailCondition(field="from", operator="contains", value="acme.com"),
            ],
        ),
        actions=[
            EmailAction(
                type="email.label",
                connector="gmail",
                label="Client/ACME",
                message_id="${trigger.email.id}",
            )
        ],
    )


# =============================================================================
# TriggerMatcher Tests
# =============================================================================


class TestTriggerMatcher:
    """Tests for TriggerMatcher."""

    def test_matches_from_contains(self, sample_email):
        """Test matching 'from' field with 'contains' operator."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="from", operator="contains", value="acme.com"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_from_contains_case_insensitive(self, sample_email):
        """Test that matching is case-insensitive."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="from", operator="contains", value="ACME.COM"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_does_not_match_wrong_domain(self, sample_email):
        """Test that non-matching domain returns False."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="from", operator="contains", value="other.com"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is False

    def test_matches_subject_contains(self, sample_email):
        """Test matching 'subject' field with 'contains' operator."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="subject", operator="contains", value="invoice"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_subject_equals(self, sample_email):
        """Test 'equals' operator on subject (substring match)."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="subject", operator="equals", value="ACME"),
            ],
        )

        # 'equals' still checks if value is in the field (for now)
        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_body_contains(self, sample_email):
        """Test matching 'body' field."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="body", operator="contains", value="attached invoice"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_attachments_contains(self, sample_email):
        """Test matching 'attachments' field (filename)."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="attachments", operator="contains", value=".pdf"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_regex_pattern(self, sample_email):
        """Test 'matches' operator with regex pattern."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="subject", operator="matches", value=r"Invoice #\d+"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_regex_no_match(self, sample_email):
        """Test 'matches' operator when regex doesn't match."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="subject", operator="matches", value=r"^Quote #\d+"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is False

    def test_matches_multiple_conditions_all_match(self, sample_email):
        """Test that ALL conditions must match."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="from", operator="contains", value="acme.com"),
                EmailCondition(field="subject", operator="contains", value="invoice"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_multiple_conditions_one_fails(self, sample_email):
        """Test that if one condition fails, the whole match fails."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="from", operator="contains", value="acme.com"),
                EmailCondition(field="subject", operator="contains", value="quote"),  # Won't match
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is False

    def test_matches_empty_conditions(self, sample_email):
        """Test that empty conditions matches all emails."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(account="test", conditions=[])

        assert matcher.matches_email(sample_email, trigger) is True

    def test_matches_invalid_regex_returns_false(self, sample_email):
        """Test that invalid regex patterns return False (not raise)."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="subject", operator="matches", value="[invalid"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is False

    def test_matches_to_field(self, sample_email):
        """Test matching 'to' field."""
        matcher = TriggerMatcher()
        trigger = EmailTrigger(
            account="test",
            conditions=[
                EmailCondition(field="to", operator="contains", value="example.com"),
            ],
        )

        assert matcher.matches_email(sample_email, trigger) is True


# =============================================================================
# EmailWatcher Tests
# =============================================================================


class TestEmailWatcher:
    """Tests for EmailWatcher."""

    def test_is_email_trigger_with_email_trigger_object(self, sample_automation):
        """Test _is_email_trigger with EmailTrigger object."""
        watcher = EmailWatcher()
        assert watcher._is_email_trigger(sample_automation) is True

    def test_is_email_trigger_with_dict_trigger(self):
        """Test _is_email_trigger with dict trigger."""
        watcher = EmailWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={"type": "email", "account": "test", "conditions": []},
            actions=[],
        )
        assert watcher._is_email_trigger(automation) is True

    def test_is_email_trigger_with_non_email_trigger(self):
        """Test _is_email_trigger with non-email trigger."""
        watcher = EmailWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={"type": "schedule", "cron": "0 9 * * *"},
            actions=[],
        )
        assert watcher._is_email_trigger(automation) is False

    def test_get_email_trigger_from_object(self, sample_automation):
        """Test _get_email_trigger with EmailTrigger object."""
        watcher = EmailWatcher()
        trigger = watcher._get_email_trigger(sample_automation)

        assert trigger is not None
        assert isinstance(trigger, EmailTrigger)
        assert len(trigger.conditions) == 1
        assert trigger.conditions[0].value == "acme.com"

    def test_get_email_trigger_from_dict(self):
        """Test _get_email_trigger with dict trigger."""
        watcher = EmailWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={
                "type": "email",
                "account": "test@example.com",
                "conditions": [
                    {"field": "from", "operator": "contains", "value": "client.com"}
                ],
            },
            actions=[],
        )

        trigger = watcher._get_email_trigger(automation)

        assert trigger is not None
        assert isinstance(trigger, EmailTrigger)
        assert trigger.account == "test@example.com"
        assert len(trigger.conditions) == 1
        assert trigger.conditions[0].field == "from"
        assert trigger.conditions[0].value == "client.com"

    def test_get_email_trigger_returns_none_for_non_email(self):
        """Test _get_email_trigger returns None for non-email triggers."""
        watcher = EmailWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={"type": "schedule", "cron": "0 9 * * *"},
            actions=[],
        )

        assert watcher._get_email_trigger(automation) is None

    def test_build_gmail_query_first_run(self):
        """Test Gmail query building on first run (no last_check)."""
        watcher = EmailWatcher()
        watcher._last_check = None

        query = watcher._build_gmail_query()

        assert query.startswith("in:inbox after:")

    def test_build_gmail_query_with_last_check(self):
        """Test Gmail query building with last_check set."""
        watcher = EmailWatcher()
        watcher._last_check = datetime(2024, 1, 15, 12, 0, 0)

        query = watcher._build_gmail_query()

        assert "in:inbox" in query
        assert "after:" in query
        # Timestamp should be approximately correct
        expected_timestamp = int(watcher._last_check.timestamp())
        assert str(expected_timestamp) in query


# =============================================================================
# Integration Tests (with mocks)
# =============================================================================


class TestWatcherIntegration:
    """Integration tests for EmailWatcher with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_execute_automation_creates_trigger_event(self, sample_email, sample_automation):
        """Test that _execute_automation creates proper TriggerEvent."""
        watcher = EmailWatcher()

        # Mock the execution engine
        with patch.object(watcher._engine, 'run', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = MagicMock(status=MagicMock(value="success"), error=None)

            await watcher._execute_automation(sample_automation, sample_email)

            # Verify run was called
            mock_run.assert_called_once()

            # Check the trigger event
            call_args = mock_run.call_args
            trigger_event = call_args[0][1]  # Second positional arg

            assert trigger_event.type == "email"
            assert trigger_event.data["email"]["id"] == "msg_123"
            assert trigger_event.data["email"]["subject"] == "Invoice #1234 from ACME Corp"
            assert trigger_event.data["email"]["from"] == "john@acme.com"
            assert trigger_event.data["email"]["from_domain"] == "acme.com"

    @pytest.mark.asyncio
    async def test_poll_processes_matching_emails(self, sample_email, sample_automation):
        """Test that _poll processes emails that match triggers."""
        watcher = EmailWatcher()
        watcher._processed_ids = set()

        # Mock dependencies
        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [sample_automation]

        mock_client = AsyncMock()
        mock_client.search.return_value = MagicMock(emails=[sample_email])

        with patch('pai.watcher.get_db', return_value=mock_db), \
             patch.object(watcher, '_client', mock_client), \
             patch.object(watcher, '_execute_automation', new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, '_save_state', new_callable=AsyncMock):

            await watcher._poll()

            # Verify automation was executed
            mock_execute.assert_called_once_with(sample_automation, sample_email)

            # Verify email was marked as processed
            assert sample_email.id in watcher._processed_ids

    @pytest.mark.asyncio
    async def test_poll_skips_already_processed_emails(self, sample_email, sample_automation):
        """Test that _poll skips already processed emails."""
        watcher = EmailWatcher()
        watcher._processed_ids = {sample_email.id}  # Already processed

        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [sample_automation]

        mock_client = AsyncMock()
        mock_client.search.return_value = MagicMock(emails=[sample_email])

        with patch('pai.watcher.get_db', return_value=mock_db), \
             patch.object(watcher, '_client', mock_client), \
             patch.object(watcher, '_execute_automation', new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, '_save_state', new_callable=AsyncMock):

            await watcher._poll()

            # Verify automation was NOT executed
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_skips_non_matching_emails(self, sample_email):
        """Test that _poll skips emails that don't match trigger conditions."""
        watcher = EmailWatcher()
        watcher._processed_ids = set()

        # Automation with non-matching trigger
        automation = Automation(
            id="auto_1",
            name="Test",
            status=AutomationStatus.ACTIVE,
            trigger=EmailTrigger(
                account="test",
                conditions=[
                    EmailCondition(field="from", operator="contains", value="other.com"),
                ],
            ),
            actions=[],
        )

        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [automation]

        mock_client = AsyncMock()
        mock_client.search.return_value = MagicMock(emails=[sample_email])

        with patch('pai.watcher.get_db', return_value=mock_db), \
             patch.object(watcher, '_client', mock_client), \
             patch.object(watcher, '_execute_automation', new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, '_save_state', new_callable=AsyncMock):

            await watcher._poll()

            # Verify automation was NOT executed (email doesn't match)
            mock_execute.assert_not_called()

            # But email should still be marked as processed
            assert sample_email.id in watcher._processed_ids
