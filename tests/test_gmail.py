"""Tests for the Gmail connector."""

import pytest

from pai.gmail import (
    Email,
    EmailAddress,
    Attachment,
    EntityExtractor,
    SearchResult,
)
from pai.models import Entity, EntityType


class TestEmailAddress:
    """Tests for email address parsing."""

    def test_parse_full_address(self):
        """Test parsing 'Name <email@domain.com>' format."""
        addr = EmailAddress.parse("John Doe <john@acme.com>")
        assert addr.name == "John Doe"
        assert addr.email == "john@acme.com"
        assert addr.domain == "acme.com"

    def test_parse_email_only(self):
        """Test parsing just email address."""
        addr = EmailAddress.parse("jane@example.org")
        assert addr.name == ""
        assert addr.email == "jane@example.org"
        assert addr.domain == "example.org"

    def test_parse_empty(self):
        """Test parsing empty string."""
        addr = EmailAddress.parse("")
        assert addr.email == ""
        assert addr.domain == ""


class TestEntityExtractor:
    """Tests for entity extraction from emails."""

    @pytest.fixture
    def sample_emails(self) -> list[Email]:
        """Create sample emails for testing."""
        return [
            Email(
                id="msg_1",
                thread_id="thread_1",
                subject="Invoice #123",
                **{"from": EmailAddress(name="John", email="john@acme.com", domain="acme.com")},
                to=[EmailAddress(email="me@gmail.com", domain="gmail.com")],
            ),
            Email(
                id="msg_2",
                thread_id="thread_2",
                subject="Meeting notes",
                **{"from": EmailAddress(name="Jane", email="jane@acme.com", domain="acme.com")},
                to=[EmailAddress(email="me@gmail.com", domain="gmail.com")],
            ),
            Email(
                id="msg_3",
                thread_id="thread_3",
                subject="Quote request",
                **{"from": EmailAddress(name="Bob", email="bob@betacorp.io", domain="betacorp.io")},
                to=[EmailAddress(email="me@gmail.com", domain="gmail.com")],
            ),
        ]

    def test_extract_clients_from_domains(self, sample_emails):
        """Test that unique domains become client entities."""
        extractor = EntityExtractor()
        entities = extractor.extract_from_emails(sample_emails)

        # Should have 2 clients (acme.com and betacorp.io, not gmail.com)
        assert len(entities) == 2

        # Check acme.com entity (higher frequency)
        acme = next(e for e in entities if "acme" in e.name.lower())
        assert acme.type == EntityType.CLIENT
        assert acme.metadata["email_count"] == 2
        assert "acme.com" in acme.aliases

        # Check betacorp.io entity
        beta = next(e for e in entities if "betacorp" in e.name.lower())
        assert beta.type == EntityType.CLIENT
        assert beta.metadata["email_count"] == 1

    def test_ignores_common_domains(self, sample_emails):
        """Test that gmail.com and other common domains are ignored."""
        extractor = EntityExtractor()
        entities = extractor.extract_from_emails(sample_emails)

        # No gmail.com entity
        gmail_entities = [e for e in entities if "gmail" in e.name.lower()]
        assert len(gmail_entities) == 0

    def test_updates_existing_entities(self, sample_emails):
        """Test that existing entities get updated with new sources."""
        extractor = EntityExtractor()

        # First extraction
        entities = extractor.extract_from_emails(sample_emails[:1])
        acme = entities[0]
        original_source_count = len(acme.sources)

        # Second extraction with more emails
        updated = extractor.extract_from_emails(sample_emails, existing_entities=entities)
        acme_updated = next(e for e in updated if "acme" in e.name.lower())

        # Should have more sources
        assert len(acme_updated.sources) >= original_source_count

    def test_extract_people(self, sample_emails):
        """Test person entity extraction."""
        extractor = EntityExtractor()
        people = extractor.extract_people(sample_emails)

        # Should have 3 people (John, Jane, Bob - not gmail.com recipients)
        assert len(people) == 3

        names = {p.name for p in people}
        assert "John" in names
        assert "Jane" in names
        assert "Bob" in names


class TestSearchResult:
    """Tests for search result model."""

    def test_empty_result(self):
        """Test empty search result."""
        result = SearchResult(emails=[], total_estimate=0)
        assert len(result.emails) == 0
        assert result.next_page_token is None

    def test_paginated_result(self):
        """Test result with pagination."""
        result = SearchResult(
            emails=[],
            total_estimate=100,
            next_page_token="token123",
        )
        assert result.total_estimate == 100
        assert result.next_page_token == "token123"


class TestEmailModel:
    """Tests for Email model."""

    def test_email_with_attachments(self):
        """Test email with attachments."""
        email = Email(
            id="msg_1",
            thread_id="thread_1",
            **{"from": EmailAddress(email="test@test.com", domain="test.com")},
            attachments=[
                Attachment(
                    id="att_1",
                    filename="invoice.pdf",
                    mime_type="application/pdf",
                    size=1024,
                )
            ],
        )
        assert len(email.attachments) == 1
        assert email.attachments[0].filename == "invoice.pdf"

    def test_email_defaults(self):
        """Test email default values."""
        email = Email(
            id="msg_1",
            thread_id="thread_1",
            **{"from": EmailAddress(email="test@test.com", domain="test.com")},
        )
        assert email.subject == ""
        assert email.body_text == ""
        assert email.labels == []
        assert email.attachments == []
