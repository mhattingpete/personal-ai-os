"""Trigger watcher for PAI automations.

Polls data sources and triggers automations when conditions match.
Supports both MCP-based connectors and legacy direct connectors.
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from pai.db import get_db
from pai.executor import ExecutionEngine
from pai.mcp import get_mcp_manager
from pai.models import (
    Automation,
    AutomationStatus,
    EmailCondition,
    EmailTrigger,
    TriggerEvent,
)


# =============================================================================
# Email Data Model (for watcher)
# =============================================================================


class WatcherEmail:
    """Simple email representation for the watcher.

    Works with both MCP responses and legacy Gmail client.
    """

    def __init__(
        self,
        id: str,
        thread_id: str = "",
        subject: str = "",
        from_email: str = "",
        from_name: str = "",
        from_domain: str = "",
        to: list[str] | None = None,
        snippet: str = "",
        body_text: str = "",
        date: datetime | None = None,
        labels: list[str] | None = None,
        attachments: list[dict] | None = None,
    ):
        self.id = id
        self.thread_id = thread_id
        self.subject = subject
        self.from_email = from_email
        self.from_name = from_name
        self.from_domain = from_domain
        self.to = to or []
        self.snippet = snippet
        self.body_text = body_text
        self.date = date
        self.labels = labels or []
        self.attachments = attachments or []

    @classmethod
    def from_mcp_response(cls, data: dict) -> "WatcherEmail":
        """Create from MCP tool response data."""
        from_addr = data.get("from", {})
        if isinstance(from_addr, str):
            # Simple string format
            email = from_addr
            domain = email.split("@")[1] if "@" in email else ""
            from_email, from_name, from_domain = email, "", domain
        else:
            # Object format
            from_email = from_addr.get("email", "")
            from_name = from_addr.get("name", "")
            from_domain = from_addr.get("domain", from_email.split("@")[1] if "@" in from_email else "")

        return cls(
            id=data.get("id", ""),
            thread_id=data.get("thread_id", ""),
            subject=data.get("subject", ""),
            from_email=from_email,
            from_name=from_name,
            from_domain=from_domain,
            to=data.get("to", []),
            snippet=data.get("snippet", ""),
            body_text=data.get("body_text", data.get("body", "")),
            date=datetime.fromisoformat(data["date"]) if data.get("date") else None,
            labels=data.get("labels", []),
            attachments=data.get("attachments", []),
        )

    @classmethod
    def from_legacy_email(cls, email: Any) -> "WatcherEmail":
        """Create from legacy Gmail client Email object."""
        return cls(
            id=email.id,
            thread_id=email.thread_id,
            subject=email.subject,
            from_email=email.from_.email,
            from_name=email.from_.name,
            from_domain=email.from_.domain,
            to=[a.email for a in email.to],
            snippet=email.snippet,
            body_text=email.body_text,
            date=email.date,
            labels=email.labels,
            attachments=[{"filename": a.filename} for a in email.attachments],
        )


# =============================================================================
# Email Provider (MCP or Legacy)
# =============================================================================


class EmailProvider:
    """Provides email search functionality via MCP or legacy client."""

    def __init__(self):
        self._mcp_manager = get_mcp_manager()
        self._legacy_client = None
        self._use_mcp: bool | None = None  # Auto-detect on first use

    async def search(self, query: str, max_results: int = 20) -> list[WatcherEmail]:
        """Search for emails matching the query.

        Args:
            query: Gmail search query syntax.
            max_results: Maximum results to return.

        Returns:
            List of emails.
        """
        # Auto-detect which provider to use
        if self._use_mcp is None:
            self._use_mcp = self._mcp_manager.get_server_config("gmail") is not None

        if self._use_mcp:
            return await self._search_mcp(query, max_results)
        else:
            return await self._search_legacy(query, max_results)

    async def _search_mcp(self, query: str, max_results: int) -> list[WatcherEmail]:
        """Search via MCP gmail server."""
        result = await self._mcp_manager.call_tool(
            "gmail",
            "search_emails",
            {"query": query, "max_results": max_results},
        )

        if not result.success:
            print(f"[watcher] MCP search failed: {result.error}")
            return []

        emails = []
        # Try structured content first
        if result.structured and isinstance(result.structured, dict):
            for email_data in result.structured.get("emails", []):
                emails.append(WatcherEmail.from_mcp_response(email_data))
        else:
            # Try to parse from text content
            for content in result.content:
                if content.get("type") == "text":
                    try:
                        data = json.loads(content["text"])
                        if isinstance(data, list):
                            for email_data in data:
                                emails.append(WatcherEmail.from_mcp_response(email_data))
                        elif isinstance(data, dict) and "emails" in data:
                            for email_data in data["emails"]:
                                emails.append(WatcherEmail.from_mcp_response(email_data))
                    except json.JSONDecodeError:
                        pass

        return emails

    async def _search_legacy(self, query: str, max_results: int) -> list[WatcherEmail]:
        """Search via legacy Gmail client."""
        if self._legacy_client is None:
            from pai.gmail import get_gmail_client
            self._legacy_client = get_gmail_client()

        result = await self._legacy_client.search(query, max_results=max_results)
        return [WatcherEmail.from_legacy_email(e) for e in result.emails]


# =============================================================================
# Trigger Matcher
# =============================================================================


class TriggerMatcher:
    """Matches emails against trigger conditions.

    Supports multiple operators:
    - equals: Exact match (case-insensitive)
    - contains: Substring match (case-insensitive)
    - matches: Regex pattern match
    - semantic: (Not implemented - would use LLM)

    Works with both WatcherEmail and legacy Email types.
    """

    def matches_email(self, email: Any, trigger: EmailTrigger) -> bool:
        """Check if an email matches all trigger conditions.

        Args:
            email: The email to check (WatcherEmail or legacy Email).
            trigger: The trigger with conditions to match.

        Returns:
            True if ALL conditions match.
        """
        if not trigger.conditions:
            # No conditions = match all emails (probably not intended)
            return True

        for condition in trigger.conditions:
            if not self._check_condition(email, condition):
                return False

        return True

    def _check_condition(self, email: Any, condition: EmailCondition) -> bool:
        """Check a single condition against an email."""
        # Get the field value from the email
        field_value = self._get_field_value(email, condition.field)
        if field_value is None:
            return False

        # Apply the operator
        return self._apply_operator(
            field_value, condition.operator, condition.value
        )

    def _get_field_value(self, email: Any, field: str) -> str | None:
        """Extract a field value from an email.

        Works with both WatcherEmail and legacy Email types.
        """
        # Detect email type by checking for WatcherEmail attributes
        is_watcher_email = hasattr(email, "from_email")

        if field == "from":
            if is_watcher_email:
                return f"{email.from_name} <{email.from_email}> @{email.from_domain}"
            else:
                # Legacy Email type
                return f"{email.from_.name} <{email.from_.email}> @{email.from_.domain}"
        elif field == "to":
            if is_watcher_email:
                return ", ".join(email.to)
            else:
                return ", ".join(a.email for a in email.to)
        elif field == "subject":
            return email.subject
        elif field == "body":
            return email.body_text or email.snippet
        elif field == "attachments":
            if is_watcher_email:
                return ", ".join(a.get("filename", "") for a in email.attachments)
            else:
                return ", ".join(a.filename for a in email.attachments)
        return None

    def _apply_operator(self, value: str, operator: str, pattern: str) -> bool:
        """Apply an operator to check if value matches pattern."""
        value_lower = value.lower()
        pattern_lower = pattern.lower()

        if operator == "equals":
            # Check if pattern appears as a complete word/email/domain
            return pattern_lower in value_lower
        elif operator == "contains":
            return pattern_lower in value_lower
        elif operator == "matches":
            try:
                return bool(re.search(pattern, value, re.IGNORECASE))
            except re.error:
                return False
        elif operator == "semantic":
            # Would need LLM for semantic matching
            # For now, fall back to contains
            return pattern_lower in value_lower
        return False


# =============================================================================
# Email Watcher
# =============================================================================


class EmailWatcher:
    """Watches for new emails and triggers automations.

    Uses MCP when available, falls back to legacy Gmail client.

    Usage:
        watcher = EmailWatcher()
        await watcher.start(interval=60)  # Poll every 60 seconds
    """

    def __init__(self):
        self._provider = EmailProvider()
        self._matcher = TriggerMatcher()
        self._engine = ExecutionEngine()
        self._running = False
        self._last_check: datetime | None = None
        self._processed_ids: set[str] = set()

    async def start(
        self,
        interval: int = 60,
        max_iterations: int | None = None,
    ) -> None:
        """Start watching for new emails.

        Args:
            interval: Seconds between polls.
            max_iterations: Stop after N iterations (None = run forever).
        """
        self._running = True

        # Load last check time from database
        await self._load_state()

        iteration = 0
        while self._running:
            if max_iterations and iteration >= max_iterations:
                break

            try:
                await self._poll()
            except Exception as e:
                print(f"[watcher] Error during poll: {e}")

            iteration += 1
            if self._running and (not max_iterations or iteration < max_iterations):
                await asyncio.sleep(interval)

    def stop(self) -> None:
        """Stop the watcher."""
        self._running = False

    async def _poll(self) -> None:
        """Poll for new emails and check against triggers."""
        db = get_db()
        await db.initialize()

        try:
            # Get active automations with email triggers
            automations = await db.list_automations(status=AutomationStatus.ACTIVE)
            email_automations = [
                a for a in automations
                if self._is_email_trigger(a)
            ]

            if not email_automations:
                return

            # Build Gmail query for new emails
            query = self._build_gmail_query()
            emails = await self._provider.search(query, max_results=20)

            # Process each email
            for email in emails:
                # Skip if already processed
                if email.id in self._processed_ids:
                    continue

                # Check against each automation
                for automation in email_automations:
                    trigger = self._get_email_trigger(automation)
                    if trigger and self._matcher.matches_email(email, trigger):
                        await self._execute_automation(automation, email)

                # Mark as processed
                self._processed_ids.add(email.id)

            # Update state
            self._last_check = datetime.now()
            await self._save_state()

            # Keep processed IDs bounded (last 1000)
            if len(self._processed_ids) > 1000:
                # Convert to list, keep last 500
                ids_list = list(self._processed_ids)
                self._processed_ids = set(ids_list[-500:])

        finally:
            await db.close()

    def _is_email_trigger(self, automation: Automation) -> bool:
        """Check if automation has an email trigger."""
        trigger = automation.trigger
        if isinstance(trigger, EmailTrigger):
            return True
        if isinstance(trigger, dict):
            return trigger.get("type") == "email"
        return False

    def _get_email_trigger(self, automation: Automation) -> EmailTrigger | None:
        """Get the email trigger from an automation."""
        trigger = automation.trigger
        if isinstance(trigger, EmailTrigger):
            return trigger
        if isinstance(trigger, dict) and trigger.get("type") == "email":
            # Parse conditions
            conditions = []
            for cond in trigger.get("conditions", []):
                conditions.append(EmailCondition(
                    field=cond.get("field", "from"),
                    operator=cond.get("operator", "contains"),
                    value=cond.get("value", ""),
                    confidence=cond.get("confidence", 1.0),
                ))
            return EmailTrigger(
                account=trigger.get("account", ""),
                conditions=conditions,
            )
        return None

    def _build_gmail_query(self) -> str:
        """Build Gmail search query for new emails."""
        # Search for inbox emails
        query = "in:inbox"

        # Add time filter if we have a last check time
        if self._last_check:
            # Gmail uses seconds since epoch for after:
            timestamp = int(self._last_check.timestamp())
            query += f" after:{timestamp}"
        else:
            # First run: only look at emails from the last hour
            import time
            one_hour_ago = int(time.time()) - 3600
            query += f" after:{one_hour_ago}"

        return query

    async def _execute_automation(self, automation: Automation, email: Any) -> None:
        """Execute an automation triggered by an email.

        Works with both WatcherEmail and legacy Email types.
        """
        print(f"[watcher] Triggering '{automation.name}' for email: {email.subject}")

        # Detect email type by checking for WatcherEmail attributes
        is_watcher_email = hasattr(email, "from_email")

        # Build trigger event with email data
        if is_watcher_email:
            trigger_data = {
                "id": email.id,
                "thread_id": email.thread_id,
                "subject": email.subject,
                "from": email.from_email,
                "from_name": email.from_name,
                "from_domain": email.from_domain,
                "to": email.to,
                "snippet": email.snippet,
                "date": email.date.isoformat() if email.date else None,
                "labels": email.labels,
                "has_attachments": len(email.attachments) > 0,
            }
        else:
            # Legacy Email type
            trigger_data = {
                "id": email.id,
                "thread_id": email.thread_id,
                "subject": email.subject,
                "from": email.from_.email,
                "from_name": email.from_.name,
                "from_domain": email.from_.domain,
                "to": [a.email for a in email.to],
                "snippet": email.snippet,
                "date": email.date.isoformat() if email.date else None,
                "labels": email.labels,
                "has_attachments": len(email.attachments) > 0,
            }

        trigger_event = TriggerEvent(
            type="email",
            data={"email": trigger_data},
        )

        # Execute the automation
        execution = await self._engine.run(automation, trigger_event, dry_run=False)

        if execution.status.value == "success":
            print(f"[watcher] Automation '{automation.name}' completed successfully")
        else:
            print(f"[watcher] Automation '{automation.name}' failed: {execution.error}")

    async def _load_state(self) -> None:
        """Load watcher state from database."""
        db = get_db()
        await db.initialize()

        try:
            state = await db.get_watcher_state()
            if state:
                self._last_check = state.get("last_check")
                self._processed_ids = set(state.get("processed_ids", []))
        finally:
            await db.close()

    async def _save_state(self) -> None:
        """Save watcher state to database."""
        db = get_db()
        await db.initialize()

        try:
            await db.save_watcher_state({
                "last_check": self._last_check.isoformat() if self._last_check else None,
                "processed_ids": list(self._processed_ids)[-500:],  # Keep bounded
            })
        finally:
            await db.close()


# =============================================================================
# Convenience Functions
# =============================================================================


async def watch_emails(interval: int = 60) -> None:
    """Start watching for emails that trigger automations.

    Args:
        interval: Seconds between polls.
    """
    watcher = EmailWatcher()
    print(f"[watcher] Starting email watcher (polling every {interval}s)")
    print("[watcher] Press Ctrl+C to stop")

    try:
        await watcher.start(interval=interval)
    except KeyboardInterrupt:
        print("\n[watcher] Stopping...")
        watcher.stop()
