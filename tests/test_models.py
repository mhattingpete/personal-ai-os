"""Tests for PAI models."""

from pai.models import (
    Automation,
    AutomationStatus,
    EmailCondition,
    EmailTrigger,
    Entity,
    EntityType,
    FileAction,
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
