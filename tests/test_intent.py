"""Tests for the Intent Engine."""

import pytest

from pai.intent import (
    ClarifyResult,
    IntentEngine,
    LLMClarifyResponse,
    LLMParseResponse,
    LLMPlanResponse,
    ParseResult,
    PlanResult,
    ParsedAction,
    ParsedAmbiguity,
    ParsedTrigger,
)
from pai.llm import Message, Response
from pai.models import (
    ClarificationAnswer,
    IntentGraph,
    IntentTrigger,
    IntentAction,
)


class MockProvider:
    """Mock LLM provider for testing."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, type]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Response:
        return Response(
            content="mock response",
            model="mock",
        )

    async def complete_structured(
        self,
        messages: list[Message],
        schema: type,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        self.calls.append((system or "", schema))

        if schema == LLMParseResponse:
            return self.responses.get("parse", LLMParseResponse(
                intent_type="automation",
                trigger=ParsedTrigger(type="email", confidence=0.9),
                actions=[ParsedAction(type="email.label", params={"label": "Client"}, confidence=0.85)],
                ambiguities=[],
                confidence=0.85,
            ))
        elif schema == LLMClarifyResponse:
            return self.responses.get("clarify", LLMClarifyResponse(questions=[]))
        elif schema == LLMPlanResponse:
            return self.responses.get("plan", LLMPlanResponse(
                name="Label Client Emails",
                description="Labels incoming emails from clients",
                trigger_type="email",
                trigger_config={"conditions": [{"field": "from", "value": "@client.com"}]},
                actions=[{"type": "email.label", "connector": "gmail", "label": "Client"}],
                variables=[],
                error_handling=[],
                summary="This automation labels emails from clients automatically.",
            ))

        raise ValueError(f"Unknown schema: {schema}")


@pytest.mark.asyncio
async def test_parse_simple_intent():
    """Test parsing a simple intent."""
    provider = MockProvider()
    engine = IntentEngine(provider)

    result = await engine.parse("Label emails from clients")

    assert isinstance(result, ParseResult)
    assert result.intent.type == "automation"
    assert result.intent.trigger is not None
    assert result.intent.trigger.type == "email"
    assert len(result.intent.actions) == 1
    assert result.intent.actions[0].type == "email.label"


@pytest.mark.asyncio
async def test_parse_with_ambiguities():
    """Test parsing an intent with ambiguities."""
    provider = MockProvider(responses={
        "parse": LLMParseResponse(
            intent_type="automation",
            trigger=ParsedTrigger(type="email", confidence=0.6),
            actions=[ParsedAction(type="file.write", confidence=0.5)],
            ambiguities=[
                ParsedAmbiguity(
                    field="trigger.from",
                    description="Which client should trigger this?",
                    suggested_questions=["Which client emails should trigger this automation?"],
                ),
            ],
            confidence=0.5,
        ),
    })
    engine = IntentEngine(provider)

    result = await engine.parse("When a client emails me, save the attachment")

    assert result.needs_clarification is True
    assert result.ready_for_planning is False
    assert len(result.intent.ambiguities) == 1
    assert result.intent.confidence < 0.7


@pytest.mark.asyncio
async def test_clarify_generates_questions():
    """Test that clarify generates questions for ambiguities."""
    provider = MockProvider(responses={
        "clarify": LLMClarifyResponse(
            questions=[
                {"question": "Which client?", "options": ["Acme", "Beta"], "default": "Acme", "ambiguity_field": "client"},
            ]
        ),
    })
    engine = IntentEngine(provider)

    intent = IntentGraph(
        id="test_intent",
        type="automation",
        confidence=0.5,
        raw_input="test",
        ambiguities=[
            {"id": "amb_1", "field": "client", "description": "Which client?", "suggested_questions": []},
        ],
    )

    result = await engine.clarify(intent)

    assert isinstance(result, ClarifyResult)
    assert len(result.questions) == 1
    assert result.questions[0].question == "Which client?"
    assert "Acme" in result.questions[0].options


@pytest.mark.asyncio
async def test_clarify_with_answers():
    """Test that clarify applies answers."""
    provider = MockProvider()
    engine = IntentEngine(provider)

    intent = IntentGraph(
        id="test_intent",
        type="automation",
        confidence=0.5,
        raw_input="test",
        ambiguities=[
            {"id": "amb_client", "field": "client", "description": "Which client?", "suggested_questions": []},
        ],
    )

    answers = [ClarificationAnswer(question_id="q_client", answer="Acme Corp")]

    result = await engine.clarify(intent, answers)

    # Ambiguities should be resolved
    assert len(result.intent.ambiguities) == 0


@pytest.mark.asyncio
async def test_plan_generates_automation():
    """Test that plan generates a valid automation."""
    provider = MockProvider()
    engine = IntentEngine(provider)

    intent = IntentGraph(
        id="test_intent",
        type="automation",
        confidence=0.9,
        raw_input="Label emails from clients",
        trigger=IntentTrigger(type="email", conditions=[{"field": "from", "value": "@client.com"}]),
        actions=[IntentAction(type="email.label", params={"label": "Client"})],
        ambiguities=[],  # No ambiguities
    )

    result = await engine.plan(intent)

    assert isinstance(result, PlanResult)
    assert result.automation.name == "Label Client Emails"
    assert result.automation.trigger.type == "email"
    assert result.summary is not None


@pytest.mark.asyncio
async def test_plan_fails_with_ambiguities():
    """Test that plan fails if there are unresolved ambiguities."""
    provider = MockProvider()
    engine = IntentEngine(provider)

    intent = IntentGraph(
        id="test_intent",
        type="automation",
        confidence=0.5,
        raw_input="test",
        ambiguities=[
            {"id": "amb_1", "field": "client", "description": "Which client?", "suggested_questions": []},
        ],
    )

    with pytest.raises(ValueError, match="unresolved ambiguities"):
        await engine.plan(intent)


@pytest.mark.asyncio
async def test_full_pipeline():
    """Test the full parse → clarify → plan pipeline."""
    # Mock responses for each stage
    provider = MockProvider(responses={
        "parse": LLMParseResponse(
            intent_type="automation",
            trigger=ParsedTrigger(type="email", confidence=0.9),
            actions=[ParsedAction(type="email.label", params={"label": "Client"}, confidence=0.9)],
            ambiguities=[],
            confidence=0.9,
        ),
        "plan": LLMPlanResponse(
            name="Auto-Label Client Emails",
            description="Automatically labels emails from known clients",
            trigger_type="email",
            trigger_config={"conditions": [{"field": "from", "operator": "contains", "value": "@client.com"}]},
            actions=[{"type": "email.label", "connector": "gmail", "label": "Client"}],
            variables=[],
            error_handling=[],
            summary="Labels all emails from client domains with the 'Client' label.",
        ),
    })
    engine = IntentEngine(provider)

    # Stage 1: Parse
    parse_result = await engine.parse("Label emails from @client.com as Client")
    assert parse_result.ready_for_planning is True

    # Stage 3: Plan (skipping clarify since no ambiguities)
    plan_result = await engine.plan(parse_result.intent)
    assert plan_result.automation.name == "Auto-Label Client Emails"
    assert plan_result.automation.status.value == "draft"
