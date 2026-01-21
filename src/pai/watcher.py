"""Trigger watcher for PAI automations.

Polls data sources and triggers automations when conditions match.
Currently supports email triggers (Gmail polling).
"""

import asyncio
import re
from datetime import datetime
from typing import Any

from pai.db import get_db
from pai.executor import ExecutionEngine
from pai.gmail import Email, get_gmail_client
from pai.models import (
    Automation,
    AutomationStatus,
    EmailCondition,
    EmailTrigger,
    TriggerEvent,
)


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
    """

    def matches_email(self, email: Email, trigger: EmailTrigger) -> bool:
        """Check if an email matches all trigger conditions.

        Args:
            email: The email to check.
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

    def _check_condition(self, email: Email, condition: EmailCondition) -> bool:
        """Check a single condition against an email."""
        # Get the field value from the email
        field_value = self._get_field_value(email, condition.field)
        if field_value is None:
            return False

        # Apply the operator
        return self._apply_operator(
            field_value, condition.operator, condition.value
        )

    def _get_field_value(self, email: Email, field: str) -> str | None:
        """Extract a field value from an email."""
        if field == "from":
            # Check both email address and domain
            return f"{email.from_.name} <{email.from_.email}> @{email.from_.domain}"
        elif field == "to":
            return ", ".join(f"{a.email}" for a in email.to)
        elif field == "subject":
            return email.subject
        elif field == "body":
            return email.body_text or email.snippet
        elif field == "attachments":
            # Return attachment filenames for matching
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

    Usage:
        watcher = EmailWatcher()
        await watcher.start(interval=60)  # Poll every 60 seconds
    """

    def __init__(self):
        self._client = None
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
        self._client = get_gmail_client()

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
            results = await self._client.search(query, max_results=20)

            # Process each email
            for email in results.emails:
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

    async def _execute_automation(self, automation: Automation, email: Email) -> None:
        """Execute an automation triggered by an email."""
        print(f"[watcher] Triggering '{automation.name}' for email: {email.subject}")

        # Build trigger event with email data
        trigger_event = TriggerEvent(
            type="email",
            data={
                "email": {
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
            },
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
