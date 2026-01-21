"""SQLite database wrapper for PAI.

All database operations go through this module.
Uses aiosqlite for async operations.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from pai.config import get_settings
from pai.models import (
    Automation,
    AutomationStatus,
    Connector,
    ConnectorStatus,
    ConnectorType,
    Entity,
    EntityType,
    Execution,
    ExecutionStatus,
    IntentGraph,
)

# Schema version for migrations
SCHEMA_VERSION = 1

SCHEMA = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Connectors (user's connected accounts)
CREATE TABLE IF NOT EXISTS connectors (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    account_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    credentials_encrypted TEXT,
    last_sync TEXT,
    schema_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(type, account_id)
);

-- Entities (clients, projects, etc. discovered from data)
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    sources_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

-- Automations
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    trigger_json TEXT NOT NULL,
    variables_json TEXT DEFAULT '[]',
    actions_json TEXT DEFAULT '[]',
    error_handling_json TEXT DEFAULT '[]',
    monitoring_json TEXT DEFAULT '{}',
    capabilities_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_automations_status ON automations(status);

-- Executions
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    automation_version INTEGER NOT NULL,
    triggered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    trigger_event_json TEXT NOT NULL,
    variables_json TEXT DEFAULT '[]',
    action_results_json TEXT DEFAULT '[]',
    error_json TEXT,
    FOREIGN KEY (automation_id) REFERENCES automations(id)
);
CREATE INDEX IF NOT EXISTS idx_executions_automation ON executions(automation_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_triggered_at ON executions(triggered_at);

-- Intent graphs (parsed intents, for learning)
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'automation',
    confidence REAL NOT NULL DEFAULT 1.0,
    trigger_json TEXT,
    actions_json TEXT DEFAULT '[]',
    ambiguities_json TEXT DEFAULT '[]',
    raw_input TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    messages_json TEXT DEFAULT '[]',
    context_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().db.path
        self._conn: aiosqlite.Connection | None = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get a database connection."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.path)
            self._conn.row_factory = aiosqlite.Row
        yield self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def initialize(self) -> None:
        """Initialize the database schema."""
        async with self.connection() as conn:
            await conn.executescript(SCHEMA)
            # Set schema version
            await conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            await conn.commit()

    # =========================================================================
    # Connectors
    # =========================================================================

    async def save_connector(self, connector: Connector) -> None:
        """Save a connector."""
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO connectors
                (id, type, account_id, status, last_sync, schema_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connector.id,
                    connector.type.value,
                    connector.account_id,
                    connector.status.value,
                    connector.last_sync.isoformat() if connector.last_sync else None,
                    connector.schema_.model_dump_json() if connector.schema_ else None,
                    connector.created_at.isoformat(),
                ),
            )
            await conn.commit()

    async def get_connector(self, connector_id: str) -> Connector | None:
        """Get a connector by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM connectors WHERE id = ?", (connector_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_connector(row)

    async def list_connectors(
        self, status: ConnectorStatus | None = None
    ) -> list[Connector]:
        """List all connectors, optionally filtered by status."""
        async with self.connection() as conn:
            if status:
                cursor = await conn.execute(
                    "SELECT * FROM connectors WHERE status = ?", (status.value,)
                )
            else:
                cursor = await conn.execute("SELECT * FROM connectors")
            rows = await cursor.fetchall()
            return [self._row_to_connector(row) for row in rows]

    def _row_to_connector(self, row: aiosqlite.Row) -> Connector:
        """Convert a database row to a Connector model."""
        return Connector(
            id=row["id"],
            type=ConnectorType(row["type"]),
            account_id=row["account_id"],
            status=ConnectorStatus(row["status"]),
            last_sync=datetime.fromisoformat(row["last_sync"]) if row["last_sync"] else None,
            schema=json.loads(row["schema_json"]) if row["schema_json"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # =========================================================================
    # Entities
    # =========================================================================

    async def save_entity(self, entity: Entity) -> None:
        """Save an entity."""
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO entities
                (id, type, name, aliases_json, metadata_json, sources_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.id,
                    entity.type.value,
                    entity.name,
                    json.dumps(entity.aliases),
                    json.dumps(entity.metadata),
                    json.dumps([s.model_dump(mode="json") for s in entity.sources]),
                    entity.created_at.isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            await conn.commit()

    async def get_entity(self, entity_id: str) -> Entity | None:
        """Get an entity by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    async def list_entities(
        self, entity_type: EntityType | None = None
    ) -> list[Entity]:
        """List all entities, optionally filtered by type."""
        async with self.connection() as conn:
            if entity_type:
                cursor = await conn.execute(
                    "SELECT * FROM entities WHERE type = ?", (entity_type.value,)
                )
            else:
                cursor = await conn.execute("SELECT * FROM entities")
            rows = await cursor.fetchall()
            return [self._row_to_entity(row) for row in rows]

    def _row_to_entity(self, row: aiosqlite.Row) -> Entity:
        """Convert a database row to an Entity model."""
        return Entity(
            id=row["id"],
            type=EntityType(row["type"]),
            name=row["name"],
            aliases=json.loads(row["aliases_json"]),
            metadata=json.loads(row["metadata_json"]),
            sources=json.loads(row["sources_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # =========================================================================
    # Automations
    # =========================================================================

    async def save_automation(self, automation: Automation) -> None:
        """Save an automation."""
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO automations
                (id, name, description, status, trigger_json, variables_json, actions_json,
                 error_handling_json, monitoring_json, capabilities_json, created_at, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    automation.id,
                    automation.name,
                    automation.description,
                    automation.status.value,
                    automation.trigger.model_dump_json(),
                    json.dumps([v.model_dump(mode="json") for v in automation.variables]),
                    json.dumps([a.model_dump(mode="json") for a in automation.actions]),
                    json.dumps([e.model_dump(mode="json") for e in automation.error_handling]),
                    automation.monitoring.model_dump_json(),
                    json.dumps([c.model_dump(mode="json") for c in automation.capabilities]),
                    automation.created_at.isoformat(),
                    datetime.now().isoformat(),
                    automation.version,
                ),
            )
            await conn.commit()

    async def get_automation(self, automation_id: str) -> Automation | None:
        """Get an automation by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM automations WHERE id = ?", (automation_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_automation(row)

    async def delete_automation(self, automation_id: str) -> bool:
        """Delete an automation by ID.

        Returns:
            True if deleted, False if not found.
        """
        async with self.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM automations WHERE id = ?", (automation_id,)
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def list_automations(
        self, status: AutomationStatus | None = None
    ) -> list[Automation]:
        """List all automations, optionally filtered by status."""
        async with self.connection() as conn:
            if status:
                cursor = await conn.execute(
                    "SELECT * FROM automations WHERE status = ? ORDER BY updated_at DESC",
                    (status.value,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM automations ORDER BY updated_at DESC"
                )
            rows = await cursor.fetchall()
            return [self._row_to_automation(row) for row in rows]

    def _row_to_automation(self, row: aiosqlite.Row) -> Automation:
        """Convert a database row to an Automation model."""
        trigger_data = json.loads(row["trigger_json"])
        return Automation(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            status=AutomationStatus(row["status"]),
            trigger=trigger_data,
            variables=json.loads(row["variables_json"]),
            actions=json.loads(row["actions_json"]),
            error_handling=json.loads(row["error_handling_json"]),
            monitoring=json.loads(row["monitoring_json"]),
            capabilities=json.loads(row["capabilities_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
        )

    # =========================================================================
    # Executions
    # =========================================================================

    async def save_execution(self, execution: Execution) -> None:
        """Save an execution."""
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO executions
                (id, automation_id, automation_version, triggered_at, completed_at, status,
                 trigger_event_json, variables_json, action_results_json, error_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.id,
                    execution.automation_id,
                    execution.automation_version,
                    execution.triggered_at.isoformat(),
                    execution.completed_at.isoformat() if execution.completed_at else None,
                    execution.status.value,
                    execution.trigger_event.model_dump_json(),
                    json.dumps([v.model_dump(mode="json") for v in execution.variables]),
                    json.dumps([r.model_dump(mode="json") for r in execution.action_results]),
                    execution.error.model_dump_json() if execution.error else None,
                ),
            )
            await conn.commit()

    async def get_execution(self, execution_id: str) -> Execution | None:
        """Get an execution by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM executions WHERE id = ?", (execution_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_execution(row)

    async def list_executions(
        self,
        automation_id: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 100,
    ) -> list[Execution]:
        """List executions with optional filters."""
        async with self.connection() as conn:
            query = "SELECT * FROM executions WHERE 1=1"
            params: list[Any] = []

            if automation_id:
                query += " AND automation_id = ?"
                params.append(automation_id)
            if status:
                query += " AND status = ?"
                params.append(status.value)

            query += " ORDER BY triggered_at DESC LIMIT ?"
            params.append(limit)

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_execution(row) for row in rows]

    def _row_to_execution(self, row: aiosqlite.Row) -> Execution:
        """Convert a database row to an Execution model."""
        return Execution(
            id=row["id"],
            automation_id=row["automation_id"],
            automation_version=row["automation_version"],
            triggered_at=datetime.fromisoformat(row["triggered_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            status=ExecutionStatus(row["status"]),
            trigger_event=json.loads(row["trigger_event_json"]),
            variables=json.loads(row["variables_json"]),
            action_results=json.loads(row["action_results_json"]),
            error=json.loads(row["error_json"]) if row["error_json"] else None,
        )

    # =========================================================================
    # Intents
    # =========================================================================

    async def save_intent(self, intent: IntentGraph) -> None:
        """Save an intent graph."""
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO intents
                (id, type, confidence, trigger_json, actions_json, ambiguities_json, raw_input, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.id,
                    intent.type,
                    intent.confidence,
                    intent.trigger.model_dump_json() if intent.trigger else None,
                    json.dumps([a.model_dump(mode="json") for a in intent.actions]),
                    json.dumps([a.model_dump(mode="json") for a in intent.ambiguities]),
                    intent.raw_input,
                    datetime.now().isoformat(),
                ),
            )
            await conn.commit()

    async def get_intent(self, intent_id: str) -> IntentGraph | None:
        """Get an intent by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM intents WHERE id = ?", (intent_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return IntentGraph(
                id=row["id"],
                type=row["type"],
                confidence=row["confidence"],
                trigger=json.loads(row["trigger_json"]) if row["trigger_json"] else None,
                actions=json.loads(row["actions_json"]),
                ambiguities=json.loads(row["ambiguities_json"]),
                raw_input=row["raw_input"],
            )


# Global database instance
_db: Database | None = None


def get_db() -> Database:
    """Get the global database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db


async def init_db() -> Database:
    """Initialize the database and return the instance."""
    db = get_db()
    await db.initialize()
    return db
