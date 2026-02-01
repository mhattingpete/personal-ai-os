"""Tests for PAI models."""

from pai.models import (
    Automation,
    AutomationStatus,
    EmailCondition,
    EmailTrigger,
    Entity,
    EntityType,
    FileAction,
    GitHubPRCondition,
    GitHubPRTrigger,
    GitHubReviewAction,
    IntentGraph,
)


def test_email_trigger():
    """Test EmailTrigger model."""
    trigger = EmailTrigger(
        account="user@example.com",
        conditions=[
            EmailCondition(
                field="from",
                operator="contains",
                value="@client.com",
            )
        ],
    )
    assert trigger.type == "email"
    assert len(trigger.conditions) == 1


def test_automation():
    """Test Automation model."""
    auto = Automation(
        id="auto_001",
        name="Test Automation",
        description="A test automation",
        trigger=EmailTrigger(account="user@example.com"),
        actions=[
            FileAction(
                type="file.write",
                connector="google_drive",
                path="/test/file.pdf",
            )
        ],
    )
    assert auto.status == AutomationStatus.DRAFT
    assert auto.version == 1
    assert len(auto.actions) == 1


def test_entity():
    """Test Entity model."""
    entity = Entity(
        id="ent_001",
        type=EntityType.CLIENT,
        name="Acme Corp",
        aliases=["Acme", "ACME Corporation"],
        metadata={"domain": "acme.com"},
    )
    assert entity.type == EntityType.CLIENT
    assert "Acme" in entity.aliases


def test_intent_graph():
    """Test IntentGraph model."""
    intent = IntentGraph(
        id="int_001",
        confidence=0.85,
        raw_input="When a client emails me...",
    )
    assert intent.type == "automation"
    assert intent.confidence == 0.85


def test_github_pr_trigger():
    """Test GitHubPRTrigger model."""
    trigger = GitHubPRTrigger(
        account="testuser",
        conditions=[
            GitHubPRCondition(
                field="repo",
                operator="contains",
                value="my-project",
            )
        ],
        review_states=["changes_requested", "commented"],
    )
    assert trigger.type == "github_pr"
    assert len(trigger.conditions) == 1
    assert trigger.conditions[0].field == "repo"
    assert "changes_requested" in trigger.review_states


def test_github_pr_trigger_defaults():
    """Test GitHubPRTrigger default values."""
    trigger = GitHubPRTrigger(account="testuser")
    assert trigger.type == "github_pr"
    assert trigger.conditions == []
    assert "approved" in trigger.review_states
    assert "changes_requested" in trigger.review_states
    assert "commented" in trigger.review_states


def test_github_pr_condition():
    """Test GitHubPRCondition model."""
    condition = GitHubPRCondition(
        field="reviewer",
        operator="equals",
        value="senior-dev",
    )
    assert condition.field == "reviewer"
    assert condition.operator == "equals"
    assert condition.value == "senior-dev"


def test_github_review_action():
    """Test GitHubReviewAction model."""
    action = GitHubReviewAction(
        repo="owner/repo",
        pr_number=42,
        local_repo_path="/home/user/projects/repo",
        additional_instructions="Focus on security fixes",
    )
    assert action.type == "github.implement_review"
    assert action.repo == "owner/repo"
    assert action.pr_number == 42
    assert action.additional_instructions == "Focus on security fixes"


def test_github_review_action_defaults():
    """Test GitHubReviewAction default values."""
    action = GitHubReviewAction()
    assert action.type == "github.implement_review"
    assert action.repo is None
    assert action.pr_number is None
    assert action.script_id is None


def test_automation_with_github_trigger():
    """Test Automation with GitHubPRTrigger."""
    auto = Automation(
        id="auto_gh_001",
        name="Implement PR Reviews",
        description="Automatically implement PR review feedback",
        trigger=GitHubPRTrigger(
            account="myuser",
            conditions=[
                GitHubPRCondition(field="repo", operator="contains", value="my-project"),
            ],
        ),
        actions=[
            GitHubReviewAction(
                additional_instructions="Run tests after changes",
            )
        ],
    )
    assert auto.trigger.type == "github_pr"
    assert len(auto.actions) == 1
    assert auto.actions[0].type == "github.implement_review"
