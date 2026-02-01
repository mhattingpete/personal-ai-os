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
    GitHubPRCondition,
    GitHubPRTrigger,
    GitHubReviewAction,
)
from pai.watcher import EmailWatcher, GitHubPRWatcher, TriggerMatcher


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

        # Mock the provider's search to return WatcherEmail
        from pai.watcher import WatcherEmail
        watcher_email = WatcherEmail.from_legacy_email(sample_email)

        with patch('pai.watcher.get_db', return_value=mock_db), \
             patch.object(watcher._provider, 'search', new_callable=AsyncMock) as mock_search, \
             patch.object(watcher, '_execute_automation', new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, '_save_state', new_callable=AsyncMock):

            mock_search.return_value = [watcher_email]

            await watcher._poll()

            # Verify automation was executed
            mock_execute.assert_called_once()

            # Verify email was marked as processed
            assert watcher_email.id in watcher._processed_ids

    @pytest.mark.asyncio
    async def test_poll_skips_already_processed_emails(self, sample_email, sample_automation):
        """Test that _poll skips already processed emails."""
        from pai.watcher import WatcherEmail
        watcher_email = WatcherEmail.from_legacy_email(sample_email)

        watcher = EmailWatcher()
        watcher._processed_ids = {watcher_email.id}  # Already processed

        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [sample_automation]

        with patch('pai.watcher.get_db', return_value=mock_db), \
             patch.object(watcher._provider, 'search', new_callable=AsyncMock) as mock_search, \
             patch.object(watcher, '_execute_automation', new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, '_save_state', new_callable=AsyncMock):

            mock_search.return_value = [watcher_email]

            await watcher._poll()

            # Verify automation was NOT executed
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_skips_non_matching_emails(self, sample_email):
        """Test that _poll skips emails that don't match trigger conditions."""
        from pai.watcher import WatcherEmail
        watcher_email = WatcherEmail.from_legacy_email(sample_email)

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

        with patch('pai.watcher.get_db', return_value=mock_db), \
             patch.object(watcher._provider, 'search', new_callable=AsyncMock) as mock_search, \
             patch.object(watcher, '_execute_automation', new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, '_save_state', new_callable=AsyncMock):

            mock_search.return_value = [watcher_email]

            await watcher._poll()

            # Verify automation was NOT executed (email doesn't match)
            mock_execute.assert_not_called()

            # But email should still be marked as processed
            assert watcher_email.id in watcher._processed_ids


# =============================================================================
# GitHub PR Watcher Tests
# =============================================================================


@pytest.fixture
def sample_github_automation():
    """Create a sample automation with GitHub PR trigger."""
    return Automation(
        id="auto_gh_123",
        name="Implement PR Reviews",
        description="Implement PR review feedback with Claude Code",
        status=AutomationStatus.ACTIVE,
        trigger=GitHubPRTrigger(
            account="testuser",
            conditions=[
                GitHubPRCondition(field="repo", operator="contains", value="my-project"),
            ],
            review_states=["changes_requested", "commented"],
        ),
        actions=[
            GitHubReviewAction(
                additional_instructions="Run tests after changes",
            )
        ],
    )


@pytest.fixture
def sample_pr_data():
    """Create sample PR data as returned by MCP.

    Note: The watcher expects 'number' and 'repo' at the top level,
    with 'pr' containing detailed PR info from get_pr_reviews.
    """
    return {
        "repo": "testuser/my-project",
        "number": 42,  # PR number at top level for review ID tracking
        "pr": {
            "number": 42,
            "title": "Add new feature",
            "author": {"login": "testuser"},
            "headRefName": "feature-branch",
            "baseRefName": "main",
            "additions": 100,
            "deletions": 20,
            "changedFiles": 5,
        },
        "reviews": [
            {
                "id": 12345,
                "author": "reviewer1",
                "state": "CHANGES_REQUESTED",
                "body": "Please fix the typo on line 42",
            }
        ],
        "comments": [
            {
                "id": 67890,
                "author": "reviewer1",
                "path": "src/main.py",
                "line": 42,
                "body": "Typo here: 'teh' should be 'the'",
            }
        ],
    }


class TestGitHubPRWatcher:
    """Tests for GitHubPRWatcher."""

    def test_is_github_pr_trigger_with_trigger_object(self, sample_github_automation):
        """Test _is_github_pr_trigger with GitHubPRTrigger object."""
        watcher = GitHubPRWatcher()
        assert watcher._is_github_pr_trigger(sample_github_automation) is True

    def test_is_github_pr_trigger_with_dict_trigger(self):
        """Test _is_github_pr_trigger with dict trigger."""
        watcher = GitHubPRWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={"type": "github_pr", "account": "test", "conditions": []},
            actions=[],
        )
        assert watcher._is_github_pr_trigger(automation) is True

    def test_is_github_pr_trigger_with_non_github_trigger(self):
        """Test _is_github_pr_trigger with non-GitHub trigger."""
        watcher = GitHubPRWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={"type": "email", "account": "test@example.com", "conditions": []},
            actions=[],
        )
        assert watcher._is_github_pr_trigger(automation) is False

    def test_get_github_pr_trigger_from_object(self, sample_github_automation):
        """Test _get_github_pr_trigger with GitHubPRTrigger object."""
        watcher = GitHubPRWatcher()
        trigger = watcher._get_github_pr_trigger(sample_github_automation)

        assert trigger is not None
        assert isinstance(trigger, GitHubPRTrigger)
        assert len(trigger.conditions) == 1
        assert trigger.conditions[0].value == "my-project"
        assert "changes_requested" in trigger.review_states

    def test_get_github_pr_trigger_from_dict(self):
        """Test _get_github_pr_trigger with dict trigger."""
        watcher = GitHubPRWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={
                "type": "github_pr",
                "account": "testuser",
                "conditions": [
                    {"field": "repo", "operator": "contains", "value": "project"}
                ],
                "review_states": ["approved"],
            },
            actions=[],
        )

        trigger = watcher._get_github_pr_trigger(automation)

        assert trigger is not None
        assert isinstance(trigger, GitHubPRTrigger)
        assert trigger.account == "testuser"
        assert len(trigger.conditions) == 1
        assert trigger.conditions[0].field == "repo"
        assert trigger.review_states == ["approved"]

    def test_get_github_pr_trigger_returns_none_for_non_github(self):
        """Test _get_github_pr_trigger returns None for non-GitHub triggers."""
        watcher = GitHubPRWatcher()
        automation = Automation(
            id="auto_1",
            name="Test",
            trigger={"type": "email", "account": "test@example.com"},
            actions=[],
        )

        assert watcher._get_github_pr_trigger(automation) is None


class TestGitHubPRTriggerMatching:
    """Tests for GitHub PR trigger matching."""

    def test_matches_pr_review_changes_requested(self, sample_github_automation, sample_pr_data):
        """Test matching a PR with changes_requested review."""
        watcher = GitHubPRWatcher()
        trigger = watcher._get_github_pr_trigger(sample_github_automation)
        review = sample_pr_data["reviews"][0]

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is True

    def test_matches_pr_review_wrong_state(self, sample_github_automation, sample_pr_data):
        """Test that review with wrong state doesn't match."""
        watcher = GitHubPRWatcher()
        trigger = watcher._get_github_pr_trigger(sample_github_automation)
        review = {"state": "APPROVED", "author": "reviewer1"}

        # Trigger only accepts changes_requested and commented, not approved
        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is False

    def test_matches_pr_review_repo_condition(self, sample_pr_data):
        """Test matching repo condition."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[
                GitHubPRCondition(field="repo", operator="contains", value="my-project"),
            ],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is True

    def test_does_not_match_wrong_repo(self, sample_pr_data):
        """Test that wrong repo doesn't match."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[
                GitHubPRCondition(field="repo", operator="contains", value="other-project"),
            ],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is False

    def test_matches_reviewer_condition(self, sample_pr_data):
        """Test matching reviewer condition."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[
                GitHubPRCondition(field="reviewer", operator="equals", value="reviewer1"),
            ],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is True

    def test_matches_multiple_conditions(self, sample_pr_data):
        """Test that all conditions must match."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[
                GitHubPRCondition(field="repo", operator="contains", value="my-project"),
                GitHubPRCondition(field="reviewer", operator="equals", value="reviewer1"),
            ],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is True

    def test_fails_when_one_condition_doesnt_match(self, sample_pr_data):
        """Test that if one condition fails, the whole match fails."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[
                GitHubPRCondition(field="repo", operator="contains", value="my-project"),
                GitHubPRCondition(field="reviewer", operator="equals", value="other-reviewer"),
            ],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is False

    def test_matches_empty_conditions(self, sample_pr_data):
        """Test that empty conditions matches all PRs (with correct state)."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "anyone"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is True

    def test_matches_regex_pattern(self, sample_pr_data):
        """Test matching with regex pattern."""
        watcher = GitHubPRWatcher()
        trigger = GitHubPRTrigger(
            account="test",
            conditions=[
                GitHubPRCondition(field="repo", operator="matches", value=r"testuser/.*-project"),
            ],
            review_states=["changes_requested"],
        )
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._matches_pr_review(sample_pr_data, review, trigger) is True

    def test_check_pr_condition_title(self, sample_pr_data):
        """Test checking title condition."""
        watcher = GitHubPRWatcher()
        condition = GitHubPRCondition(field="title", operator="contains", value="new feature")
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        assert watcher._check_pr_condition(sample_pr_data, review, condition) is True

    def test_check_pr_condition_missing_author_in_pr(self, sample_pr_data):
        """Test condition check when author is missing from PR data."""
        watcher = GitHubPRWatcher()
        condition = GitHubPRCondition(field="author", operator="contains", value="someone")
        review = {"state": "CHANGES_REQUESTED", "author": "reviewer1"}

        # PR data without author info should still work (checks pr.author.login)
        pr_without_author = {**sample_pr_data, "pr": {"number": 42, "title": "Test"}}
        # With missing nested data, should return False (not crash)
        assert watcher._check_pr_condition(pr_without_author, review, condition) is False


class TestGitHubWatcherIntegration:
    """Integration tests for GitHubPRWatcher with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_execute_automation_creates_trigger_event(
        self, sample_github_automation, sample_pr_data
    ):
        """Test that _execute_automation creates proper TriggerEvent."""
        watcher = GitHubPRWatcher()
        review = sample_pr_data["reviews"][0]

        # Mock the execution engine and format_for_claude
        with patch.object(watcher._engine, "run", new_callable=AsyncMock) as mock_run, \
             patch.object(watcher, "_format_for_claude", new_callable=AsyncMock) as mock_format:
            mock_run.return_value = MagicMock(status=MagicMock(value="success"), error=None)
            mock_format.return_value = {"prompt": "Test prompt", "files_changed": ["file.py"]}

            await watcher._execute_automation(sample_github_automation, sample_pr_data, review)

            # Verify run was called
            mock_run.assert_called_once()

            # Check the trigger event
            call_args = mock_run.call_args
            trigger_event = call_args[0][1]

            assert trigger_event.type == "github_pr"
            assert trigger_event.data["repo"] == "testuser/my-project"
            assert trigger_event.data["pr_number"] == 42
            assert trigger_event.data["review"]["author"] == "reviewer1"
            assert trigger_event.data["review"]["state"] == "CHANGES_REQUESTED"

    @pytest.mark.asyncio
    async def test_poll_processes_matching_reviews(self, sample_github_automation, sample_pr_data):
        """Test that _poll processes reviews that match triggers."""
        watcher = GitHubPRWatcher()
        watcher._processed_review_ids = set()

        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [sample_github_automation]

        with patch("pai.watcher.get_db", return_value=mock_db), \
             patch.object(watcher, "_fetch_prs_with_reviews", new_callable=AsyncMock) as mock_fetch, \
             patch.object(watcher, "_execute_automation", new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, "_save_state", new_callable=AsyncMock):

            mock_fetch.return_value = [sample_pr_data]

            await watcher._poll()

            # Verify automation was executed
            mock_execute.assert_called_once()

            # Verify review was marked as processed
            review_id = f"{sample_pr_data['repo']}#{sample_pr_data['pr']['number']}:12345"
            assert review_id in watcher._processed_review_ids

    @pytest.mark.asyncio
    async def test_poll_skips_already_processed_reviews(
        self, sample_github_automation, sample_pr_data
    ):
        """Test that _poll skips already processed reviews."""
        watcher = GitHubPRWatcher()
        review_id = f"{sample_pr_data['repo']}#{sample_pr_data['pr']['number']}:12345"
        watcher._processed_review_ids = {review_id}

        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [sample_github_automation]

        with patch("pai.watcher.get_db", return_value=mock_db), \
             patch.object(watcher, "_fetch_prs_with_reviews", new_callable=AsyncMock) as mock_fetch, \
             patch.object(watcher, "_execute_automation", new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, "_save_state", new_callable=AsyncMock):

            mock_fetch.return_value = [sample_pr_data]

            await watcher._poll()

            # Verify automation was NOT executed
            mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_skips_non_matching_reviews(self, sample_pr_data):
        """Test that _poll skips reviews that don't match trigger conditions."""
        watcher = GitHubPRWatcher()
        watcher._processed_review_ids = set()

        # Automation that expects a different repo
        automation = Automation(
            id="auto_1",
            name="Test",
            status=AutomationStatus.ACTIVE,
            trigger=GitHubPRTrigger(
                account="test",
                conditions=[
                    GitHubPRCondition(field="repo", operator="contains", value="other-project"),
                ],
                review_states=["changes_requested"],
            ),
            actions=[],
        )

        mock_db = AsyncMock()
        mock_db.list_automations.return_value = [automation]

        with patch("pai.watcher.get_db", return_value=mock_db), \
             patch.object(watcher, "_fetch_prs_with_reviews", new_callable=AsyncMock) as mock_fetch, \
             patch.object(watcher, "_execute_automation", new_callable=AsyncMock) as mock_execute, \
             patch.object(watcher, "_save_state", new_callable=AsyncMock):

            mock_fetch.return_value = [sample_pr_data]

            await watcher._poll()

            # Verify automation was NOT executed (repo doesn't match)
            mock_execute.assert_not_called()

            # But review should still be marked as processed
            review_id = f"{sample_pr_data['repo']}#{sample_pr_data['pr']['number']}:12345"
            assert review_id in watcher._processed_review_ids
