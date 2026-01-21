"""Gmail connector for PAI.

Handles OAuth flow, email operations, and entity extraction.
All Gmail interactions go through this module.
"""

import asyncio
import base64
import json
import re
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pydantic import BaseModel, Field

from pai.config import get_config_dir
from pai.models import (
    Connector,
    ConnectorSchema,
    ConnectorStatus,
    ConnectorType,
    Entity,
    EntitySource,
    EntityType,
)

# Gmail API scopes - read-only for now, add modify later
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
]


# =============================================================================
# Data Models
# =============================================================================


class EmailAddress(BaseModel):
    """Parsed email address."""

    name: str = ""
    email: str
    domain: str = ""

    @classmethod
    def parse(cls, raw: str) -> "EmailAddress":
        """Parse email from 'Name <email@domain.com>' format."""
        name, email = parseaddr(raw)
        domain = email.split("@")[1] if "@" in email else ""
        return cls(name=name, email=email, domain=domain)


class Attachment(BaseModel):
    """Email attachment metadata."""

    id: str
    filename: str
    mime_type: str
    size: int


class Email(BaseModel):
    """Parsed email message."""

    id: str
    thread_id: str
    subject: str = ""
    from_: EmailAddress = Field(alias="from")
    to: list[EmailAddress] = Field(default_factory=list)
    cc: list[EmailAddress] = Field(default_factory=list)
    date: datetime | None = None
    snippet: str = ""
    body_text: str = ""
    body_html: str = ""
    labels: list[str] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    raw_headers: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SearchResult(BaseModel):
    """Gmail search results."""

    emails: list[Email]
    total_estimate: int
    next_page_token: str | None = None


# =============================================================================
# Gmail Client
# =============================================================================


class GmailClient:
    """Gmail API client with OAuth flow.

    Usage:
        client = GmailClient()
        await client.authenticate()  # Opens browser for OAuth
        emails = await client.search("from:client@example.com")
    """

    def __init__(self):
        self.credentials: Credentials | None = None
        self.service = None
        self._config_dir = get_config_dir()
        self._token_path = self._config_dir / "gmail_token.json"
        self._credentials_path = self._config_dir / "gmail_credentials.json"

    async def authenticate(self, force_refresh: bool = False) -> Connector:
        """Run OAuth flow and store credentials.

        Args:
            force_refresh: Force re-authentication even if token exists.

        Returns:
            Connector instance with connection status.

        Raises:
            FileNotFoundError: If credentials.json not found.
        """
        # Run OAuth in thread pool (blocking I/O)
        return await asyncio.get_event_loop().run_in_executor(
            None, self._authenticate_sync, force_refresh
        )

    def _authenticate_sync(self, force_refresh: bool = False) -> Connector:
        """Synchronous OAuth flow."""
        # Check for existing token
        if not force_refresh and self._token_path.exists():
            self.credentials = Credentials.from_authorized_user_file(
                str(self._token_path), SCOPES
            )

        # Refresh or get new credentials
        if self.credentials and self.credentials.expired and self.credentials.refresh_token:
            self.credentials.refresh(Request())
        elif not self.credentials or not self.credentials.valid:
            if not self._credentials_path.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {self._credentials_path}. "
                    "Download from Google Cloud Console and save as gmail_credentials.json"
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._credentials_path), SCOPES
            )
            self.credentials = flow.run_local_server(port=0)

        # Save token
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._token_path, "w") as f:
            f.write(self.credentials.to_json())

        # Build service
        self.service = build("gmail", "v1", credentials=self.credentials)

        # Get account info
        profile = self.service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "")

        return Connector(
            id=f"gmail_{email.replace('@', '_').replace('.', '_')}",
            type=ConnectorType.GMAIL,
            account_id=email,
            status=ConnectorStatus.ACTIVE,
            last_sync=datetime.now(),
            schema=ConnectorSchema(
                fields={"email": "string", "labels": "list[string]"},
                sample_data=[],
            ),
        )

    def _ensure_service(self) -> None:
        """Ensure the Gmail service is initialized."""
        if self.service is None:
            if self._token_path.exists():
                self.credentials = Credentials.from_authorized_user_file(
                    str(self._token_path), SCOPES
                )
                if self.credentials.expired and self.credentials.refresh_token:
                    self.credentials.refresh(Request())
                self.service = build("gmail", "v1", credentials=self.credentials)
            else:
                raise RuntimeError("Not authenticated. Run 'pai connect google' first.")

    async def search(
        self,
        query: str,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> SearchResult:
        """Search emails with Gmail query syntax.

        Args:
            query: Gmail search query (e.g., "from:client@example.com has:attachment")
            max_results: Maximum number of results.
            page_token: Token for pagination.

        Returns:
            SearchResult with emails and pagination info.
        """
        return await asyncio.get_event_loop().run_in_executor(
            None, self._search_sync, query, max_results, page_token
        )

    def _search_sync(
        self,
        query: str,
        max_results: int = 10,
        page_token: str | None = None,
    ) -> SearchResult:
        """Synchronous search implementation."""
        self._ensure_service()

        # List messages matching query
        result = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=max_results,
                pageToken=page_token,
            )
            .execute()
        )

        messages = result.get("messages", [])
        emails = []

        for msg in messages:
            email = self._get_message_sync(msg["id"])
            if email:
                emails.append(email)

        return SearchResult(
            emails=emails,
            total_estimate=result.get("resultSizeEstimate", 0),
            next_page_token=result.get("nextPageToken"),
        )

    async def get_message(self, message_id: str) -> Email | None:
        """Get a single email by ID.

        Args:
            message_id: Gmail message ID.

        Returns:
            Parsed Email or None if not found.
        """
        return await asyncio.get_event_loop().run_in_executor(
            None, self._get_message_sync, message_id
        )

    def _get_message_sync(self, message_id: str) -> Email | None:
        """Synchronous get message implementation."""
        self._ensure_service()

        try:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            return self._parse_message(msg)
        except Exception:
            return None

    def _parse_message(self, msg: dict[str, Any]) -> Email:
        """Parse raw Gmail API message into Email model."""
        headers = {}
        for header in msg.get("payload", {}).get("headers", []):
            headers[header["name"].lower()] = header["value"]

        # Parse date
        date = None
        if "date" in headers:
            try:
                from email.utils import parsedate_to_datetime

                date = parsedate_to_datetime(headers["date"])
            except Exception:
                pass

        # Parse addresses
        from_addr = EmailAddress.parse(headers.get("from", ""))
        to_addrs = [
            EmailAddress.parse(addr.strip())
            for addr in headers.get("to", "").split(",")
            if addr.strip()
        ]
        cc_addrs = [
            EmailAddress.parse(addr.strip())
            for addr in headers.get("cc", "").split(",")
            if addr.strip()
        ]

        # Extract body and attachments
        body_text, body_html, attachments = self._extract_body(msg.get("payload", {}))

        return Email(
            id=msg["id"],
            thread_id=msg.get("threadId", ""),
            subject=headers.get("subject", ""),
            **{"from": from_addr},
            to=to_addrs,
            cc=cc_addrs,
            date=date,
            snippet=msg.get("snippet", ""),
            body_text=body_text,
            body_html=body_html,
            labels=msg.get("labelIds", []),
            attachments=attachments,
            raw_headers=headers,
        )

    def _extract_body(
        self, payload: dict[str, Any]
    ) -> tuple[str, str, list[Attachment]]:
        """Extract text/html body and attachments from payload."""
        body_text = ""
        body_html = ""
        attachments: list[Attachment] = []

        def process_part(part: dict[str, Any]) -> None:
            nonlocal body_text, body_html

            mime_type = part.get("mimeType", "")
            body = part.get("body", {})
            data = body.get("data", "")

            # Check for attachment
            if body.get("attachmentId"):
                attachments.append(
                    Attachment(
                        id=body["attachmentId"],
                        filename=part.get("filename", ""),
                        mime_type=mime_type,
                        size=body.get("size", 0),
                    )
                )
                return

            # Extract text/html content
            if data:
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                if mime_type == "text/plain":
                    body_text = decoded
                elif mime_type == "text/html":
                    body_html = decoded

            # Recursively process nested parts
            for nested in part.get("parts", []):
                process_part(nested)

        process_part(payload)
        return body_text, body_html, attachments

    async def list_labels(self) -> list[dict[str, str]]:
        """List all Gmail labels.

        Returns:
            List of label dicts with id, name, type.
        """
        return await asyncio.get_event_loop().run_in_executor(
            None, self._list_labels_sync
        )

    def _list_labels_sync(self) -> list[dict[str, str]]:
        """Synchronous list labels implementation."""
        self._ensure_service()

        result = self.service.users().labels().list(userId="me").execute()
        return [
            {"id": label["id"], "name": label["name"], "type": label.get("type", "")}
            for label in result.get("labels", [])
        ]

    async def add_label(self, message_id: str, label_id: str) -> bool:
        """Add a label to a message.

        Args:
            message_id: Gmail message ID.
            label_id: Label ID to add.

        Returns:
            True if successful.
        """
        return await asyncio.get_event_loop().run_in_executor(
            None, self._add_label_sync, message_id, label_id
        )

    def _add_label_sync(self, message_id: str, label_id: str) -> bool:
        """Synchronous add label implementation."""
        self._ensure_service()

        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": [label_id]},
            ).execute()
            return True
        except Exception:
            return False

    async def remove_label(self, message_id: str, label_id: str) -> bool:
        """Remove a label from a message.

        Args:
            message_id: Gmail message ID.
            label_id: Label ID to remove.

        Returns:
            True if successful.
        """
        return await asyncio.get_event_loop().run_in_executor(
            None, self._remove_label_sync, message_id, label_id
        )

    def _remove_label_sync(self, message_id: str, label_id: str) -> bool:
        """Synchronous remove label implementation."""
        self._ensure_service()

        try:
            self.service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": [label_id]},
            ).execute()
            return True
        except Exception:
            return False

    async def archive(self, message_id: str) -> bool:
        """Archive a message (remove INBOX label).

        Args:
            message_id: Gmail message ID.

        Returns:
            True if successful.
        """
        return await self.remove_label(message_id, "INBOX")


# =============================================================================
# Entity Extraction
# =============================================================================


class EntityExtractor:
    """Extract entities (clients, people, etc.) from emails.

    Uses domain patterns and email content to identify entities.
    """

    # Common email domains to ignore for entity extraction
    IGNORE_DOMAINS = {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "live.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
    }

    def __init__(self, llm_provider=None):
        """Initialize extractor.

        Args:
            llm_provider: Optional LLM provider for semantic extraction.
        """
        self.llm = llm_provider

    def extract_from_emails(
        self, emails: list[Email], existing_entities: list[Entity] | None = None
    ) -> list[Entity]:
        """Extract entities from a batch of emails.

        Args:
            emails: List of parsed emails.
            existing_entities: Known entities to match against.

        Returns:
            List of discovered entities (may include updates to existing).
        """
        existing = {e.id: e for e in (existing_entities or [])}
        domain_counts: dict[str, dict[str, Any]] = {}

        for email in emails:
            # Extract from sender
            self._count_domain(domain_counts, email.from_, email.id)

            # Extract from recipients
            for addr in email.to + email.cc:
                self._count_domain(domain_counts, addr, email.id)

        # Convert to entities
        entities = []
        for domain, data in domain_counts.items():
            if domain in self.IGNORE_DOMAINS:
                continue

            entity_id = f"client_{domain.replace('.', '_')}"

            if entity_id in existing:
                # Update existing entity
                entity = existing[entity_id]
                # Add new sources
                for source in data["sources"]:
                    if source not in [s.value for s in entity.sources]:
                        entity.sources.append(
                            EntitySource(
                                connector_id="gmail",
                                field="email_domain",
                                value=source,
                            )
                        )
                entity.updated_at = datetime.now()
                entities.append(entity)
            else:
                # Create new entity
                entities.append(
                    Entity(
                        id=entity_id,
                        type=EntityType.CLIENT,
                        name=self._domain_to_name(domain),
                        aliases=[domain],
                        metadata={
                            "domain": domain,
                            "email_count": data["count"],
                            "sample_names": list(data["names"])[:5],
                        },
                        sources=[
                            EntitySource(
                                connector_id="gmail",
                                field="email_domain",
                                value=source,
                            )
                            for source in list(data["sources"])[:10]
                        ],
                    )
                )

        # Sort by frequency
        entities.sort(key=lambda e: e.metadata.get("email_count", 0), reverse=True)
        return entities

    def _count_domain(
        self,
        counts: dict[str, dict[str, Any]],
        addr: EmailAddress,
        email_id: str,
    ) -> None:
        """Count domain occurrences and collect metadata."""
        if not addr.domain:
            return

        domain = addr.domain.lower()
        if domain not in counts:
            counts[domain] = {"count": 0, "names": set(), "sources": set()}

        counts[domain]["count"] += 1
        if addr.name:
            counts[domain]["names"].add(addr.name)
        counts[domain]["sources"].add(email_id)

    def _domain_to_name(self, domain: str) -> str:
        """Convert domain to readable name."""
        # Remove TLD
        name = domain.rsplit(".", 1)[0]
        # Title case
        return name.replace("-", " ").replace("_", " ").title()

    def extract_people(self, emails: list[Email]) -> list[Entity]:
        """Extract person entities from emails.

        Args:
            emails: List of parsed emails.

        Returns:
            List of person entities.
        """
        people: dict[str, dict[str, Any]] = {}

        for email in emails:
            for addr in [email.from_] + email.to + email.cc:
                if not addr.email or addr.domain in self.IGNORE_DOMAINS:
                    continue

                email_lower = addr.email.lower()
                if email_lower not in people:
                    people[email_lower] = {
                        "name": addr.name or addr.email.split("@")[0],
                        "email": addr.email,
                        "domain": addr.domain,
                        "count": 0,
                    }
                people[email_lower]["count"] += 1

        entities = []
        for email_addr, data in people.items():
            entities.append(
                Entity(
                    id=f"person_{uuid4().hex[:8]}",
                    type=EntityType.PERSON,
                    name=data["name"],
                    aliases=[email_addr],
                    metadata={
                        "email": data["email"],
                        "domain": data["domain"],
                        "email_count": data["count"],
                    },
                    sources=[
                        EntitySource(
                            connector_id="gmail",
                            field="email_address",
                            value=email_addr,
                        )
                    ],
                )
            )

        # Sort by frequency
        entities.sort(key=lambda e: e.metadata.get("email_count", 0), reverse=True)
        return entities


# =============================================================================
# Convenience Functions
# =============================================================================


_client: GmailClient | None = None


def get_gmail_client() -> GmailClient:
    """Get the global Gmail client instance."""
    global _client
    if _client is None:
        _client = GmailClient()
    return _client
