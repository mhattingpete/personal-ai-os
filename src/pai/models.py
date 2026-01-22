"""Core data models for PAI.

Translated from TypeScript interfaces in personal-ai-os-spec.md.
All models use Pydantic v2 for validation and serialization.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class ConnectorType(str, Enum):
    """Supported connector types.

    DEPRECATED: With MCP architecture, connectors are dynamically defined
    in ~/.config/pai/mcp.json. This enum is kept for backward compatibility
    with existing database records and will be removed in a future version.

    New connectors should be added as MCP servers instead.
    """

    GMAIL = "gmail"
    GOOGLE_SHEETS = "google_sheets"
    GOOGLE_DRIVE = "google_drive"
    GOOGLE_CONTACTS = "google_contacts"
    GOOGLE_CALENDAR = "google_calendar"
    DROPBOX = "dropbox"
    NOTION = "notion"
    SLACK = "slack"
    # MCP-based connectors use string names directly, not this enum


class ConnectorStatus(str, Enum):
    """Connector connection status."""

    ACTIVE = "active"
    EXPIRED = "expired"
    ERROR = "error"


class EntityType(str, Enum):
    """Types of entities extracted from user data."""

    CLIENT = "client"
    PROJECT = "project"
    PERSON = "person"
    FOLDER = "folder"
    SPREADSHEET = "spreadsheet"


class AutomationStatus(str, Enum):
    """Automation lifecycle status."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class ExecutionStatus(str, Enum):
    """Execution result status."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class LearningEventType(str, Enum):
    """Types of learning signals."""

    CORRECTION = "correction"
    UNDO = "undo"
    FEEDBACK = "feedback"
    PATTERN = "pattern"


# =============================================================================
# Connectors
# =============================================================================


class ConnectorSchema(BaseModel):
    """Schema discovered from a connector's data."""

    fields: dict[str, str] = Field(default_factory=dict)
    sample_data: list[dict[str, Any]] = Field(default_factory=list)


class Connector(BaseModel):
    """User's connected account."""

    id: str
    type: ConnectorType
    account_id: str
    status: ConnectorStatus = ConnectorStatus.ACTIVE
    last_sync: datetime | None = None
    schema_: ConnectorSchema | None = Field(default=None, alias="schema")
    created_at: datetime = Field(default_factory=datetime.now)


# =============================================================================
# Entities
# =============================================================================


class EntitySource(BaseModel):
    """Where an entity was discovered."""

    connector_id: str
    field: str
    value: str
    discovered_at: datetime = Field(default_factory=datetime.now)


class Entity(BaseModel):
    """Known entity extracted from user's data."""

    id: str
    type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    sources: list[EntitySource] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# =============================================================================
# Triggers
# =============================================================================


class EmailCondition(BaseModel):
    """Condition for email trigger."""

    field: Literal["from", "to", "subject", "body", "attachments"]
    operator: Literal["equals", "contains", "matches", "semantic"]
    value: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EmailTrigger(BaseModel):
    """Trigger on email arrival."""

    type: Literal["email"] = "email"
    account: str
    conditions: list[EmailCondition] = Field(default_factory=list)


class ScheduleTrigger(BaseModel):
    """Trigger on schedule."""

    type: Literal["schedule"] = "schedule"
    cron: str | None = None
    interval_value: int | None = None
    interval_unit: Literal["minutes", "hours", "days"] | None = None
    timezone: str = "UTC"


class WebhookTrigger(BaseModel):
    """Trigger on webhook."""

    type: Literal["webhook"] = "webhook"
    endpoint: str
    secret: str | None = None


class FileChangeTrigger(BaseModel):
    """Trigger on file change."""

    type: Literal["file_change"] = "file_change"
    connector: str
    path_pattern: str
    events: list[Literal["created", "modified", "deleted"]] = Field(
        default_factory=lambda: ["created", "modified"]
    )


class ManualTrigger(BaseModel):
    """Manual trigger (user-initiated)."""

    type: Literal["manual"] = "manual"


Trigger = EmailTrigger | ScheduleTrigger | WebhookTrigger | FileChangeTrigger | ManualTrigger


# =============================================================================
# Actions
# =============================================================================


class FileAction(BaseModel):
    """File operation action."""

    type: Literal["file.read", "file.write", "file.move", "file.delete"]
    connector: str
    path: str
    source: str | None = None
    on_conflict: Literal["overwrite", "rename", "skip", "error"] = "rename"


class SpreadsheetRow(BaseModel):
    """Row data for spreadsheet action."""

    column: str
    value: str


class SpreadsheetAction(BaseModel):
    """Spreadsheet operation action."""

    type: Literal["spreadsheet.read", "spreadsheet.append", "spreadsheet.update"]
    connector: str
    spreadsheet_id: str
    sheet_name: str
    row: list[SpreadsheetRow] | None = None
    range: str | None = None


class EmailAction(BaseModel):
    """Email operation action."""

    type: Literal["email.send", "email.label", "email.archive"]
    connector: str
    to: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    label: str | None = None
    message_id: str | None = None


class ExtractionField(BaseModel):
    """Field to extract from a document."""

    name: str
    type: Literal["text", "currency", "date", "number"]
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class ExtractAction(BaseModel):
    """Document extraction action."""

    type: Literal["document.extract"] = "document.extract"
    source: str
    fields: list[ExtractionField]
    on_low_confidence: Literal["flag", "skip", "error"] = "flag"


Action = FileAction | SpreadsheetAction | EmailAction | ExtractAction


# =============================================================================
# Capabilities & Error Handling
# =============================================================================


class Capability(BaseModel):
    """What an automation can access."""

    connector: str
    operations: list[Literal["read", "write", "delete"]]
    scope: dict[str, Any] | None = None


class ErrorHandler(BaseModel):
    """Error handling configuration."""

    condition: str
    action: Literal["continue", "stop", "notify", "create_review_task"]
    message: str | None = None


class MonitoringConfig(BaseModel):
    """Monitoring configuration."""

    notify_on: list[str] = Field(default_factory=lambda: ["error"])
    daily_digest: bool = False


# =============================================================================
# Automations
# =============================================================================


class Variable(BaseModel):
    """Variable in an automation."""

    name: str
    type: str
    resolved_from: str


class Automation(BaseModel):
    """Automation definition."""

    id: str
    name: str
    description: str = ""
    status: AutomationStatus = AutomationStatus.DRAFT
    trigger: Trigger
    variables: list[Variable] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    error_handling: list[ErrorHandler] = Field(default_factory=list)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    capabilities: list[Capability] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    version: int = 1


# =============================================================================
# Executions
# =============================================================================


class TriggerEvent(BaseModel):
    """Event that triggered an execution."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class ResolvedVariable(BaseModel):
    """Variable resolved during execution."""

    name: str
    value: Any
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ActionResult(BaseModel):
    """Result of executing an action."""

    action_id: str
    status: Literal["success", "failed", "skipped"]
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None


class ExecutionError(BaseModel):
    """Execution error details."""

    message: str
    action_id: str | None = None
    recoverable: bool = True
    recovery_options: list[str] = Field(default_factory=list)


class Execution(BaseModel):
    """Execution record."""

    id: str
    automation_id: str
    automation_version: int
    triggered_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    trigger_event: TriggerEvent
    variables: list[ResolvedVariable] = Field(default_factory=list)
    action_results: list[ActionResult] = Field(default_factory=list)
    error: ExecutionError | None = None


# =============================================================================
# Intent Engine
# =============================================================================


class Ambiguity(BaseModel):
    """Ambiguity detected in intent parsing."""

    id: str
    field: str
    description: str
    suggested_questions: list[str] = Field(default_factory=list)


class IntentAction(BaseModel):
    """Action in an intent graph."""

    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    output: str | None = None


class IntentTrigger(BaseModel):
    """Trigger in an intent graph."""

    type: str
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class IntentGraph(BaseModel):
    """Parsed intent from natural language."""

    id: str
    type: Literal["automation", "query", "command"] = "automation"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    trigger: IntentTrigger | None = None
    actions: list[IntentAction] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    raw_input: str = ""


class ClarificationQuestion(BaseModel):
    """Question to clarify an ambiguity."""

    id: str
    ambiguity_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    default: str | None = None


class ClarificationAnswer(BaseModel):
    """Answer to a clarification question."""

    question_id: str
    answer: str


class ClarificationState(BaseModel):
    """State of clarification dialog."""

    intent_id: str
    questions_asked: int = 0
    questions_answered: int = 0
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    answers: list[ClarificationAnswer] = Field(default_factory=list)
    ready_to_plan: bool = False


# =============================================================================
# Learning
# =============================================================================


class ProposedFix(BaseModel):
    """Proposed fix from learning."""

    type: str
    rule: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class LearningEvent(BaseModel):
    """Learning event from user feedback."""

    id: str
    type: LearningEventType
    automation_id: str | None = None
    execution_id: str | None = None
    original: Any = None
    corrected: Any = None
    inferred_cause: str | None = None
    proposed_fix: ProposedFix | None = None
    user_response: Literal["accepted", "rejected", "modified"] | None = None
    created_at: datetime = Field(default_factory=datetime.now)


# =============================================================================
# Conversations
# =============================================================================


class Message(BaseModel):
    """Message in a conversation."""

    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ConversationContext(BaseModel):
    """Context for a conversation."""

    entities_mentioned: list[str] = Field(default_factory=list)
    automations_mentioned: list[str] = Field(default_factory=list)
    current_intent_id: str | None = None


class Conversation(BaseModel):
    """Conversation with the system."""

    id: str
    messages: list[Message] = Field(default_factory=list)
    context: ConversationContext = Field(default_factory=ConversationContext)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
