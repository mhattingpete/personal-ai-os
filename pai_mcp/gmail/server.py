"""Gmail MCP server for PAI.

Wraps the existing gmail.py client to expose Gmail operations as MCP tools.
Run with: uv run pai-gmail-mcp
"""

from mcp.server.fastmcp import FastMCP

from pai.gmail import get_gmail_client

# Create MCP server
mcp = FastMCP("PAI Gmail", json_response=True)

# Lazy-loaded client
_client = None


def _get_client():
    """Get or create the Gmail client."""
    global _client
    if _client is None:
        _client = get_gmail_client()
    return _client


# =============================================================================
# Tools
# =============================================================================


@mcp.tool()
async def search_emails(query: str, max_results: int = 10) -> dict:
    """Search emails using Gmail query syntax.

    Args:
        query: Gmail search query (e.g., "from:client@example.com", "has:attachment", "newer_than:7d")
        max_results: Maximum number of results to return (default: 10)

    Returns:
        Dict with emails list and total count
    """
    client = _get_client()
    result = await client.search(query, max_results=max_results)

    emails = []
    for email in result.emails:
        emails.append({
            "id": email.id,
            "thread_id": email.thread_id,
            "subject": email.subject,
            "from": {
                "email": email.from_.email,
                "name": email.from_.name,
                "domain": email.from_.domain,
            },
            "to": [{"email": a.email, "name": a.name} for a in email.to],
            "date": email.date.isoformat() if email.date else None,
            "snippet": email.snippet,
            "labels": email.labels,
            "has_attachments": len(email.attachments) > 0,
            "attachments": [
                {"filename": a.filename, "mime_type": a.mime_type, "size": a.size}
                for a in email.attachments
            ],
        })

    return {
        "emails": emails,
        "total_estimate": result.total_estimate,
    }


@mcp.tool()
async def get_email(message_id: str) -> dict | None:
    """Get a single email by ID.

    Args:
        message_id: Gmail message ID

    Returns:
        Email details or None if not found
    """
    client = _get_client()
    email = await client.get_message(message_id)

    if not email:
        return None

    return {
        "id": email.id,
        "thread_id": email.thread_id,
        "subject": email.subject,
        "from": {
            "email": email.from_.email,
            "name": email.from_.name,
            "domain": email.from_.domain,
        },
        "to": [{"email": a.email, "name": a.name} for a in email.to],
        "cc": [{"email": a.email, "name": a.name} for a in email.cc],
        "date": email.date.isoformat() if email.date else None,
        "snippet": email.snippet,
        "body_text": email.body_text,
        "labels": email.labels,
        "attachments": [
            {"id": a.id, "filename": a.filename, "mime_type": a.mime_type, "size": a.size}
            for a in email.attachments
        ],
    }


@mcp.tool()
async def list_labels() -> list[dict]:
    """List all Gmail labels.

    Returns:
        List of labels with id, name, and type
    """
    client = _get_client()
    return await client.list_labels()


@mcp.tool()
async def add_label(message_id: str, label: str) -> dict:
    """Add a label to an email. Creates the label if it doesn't exist.

    Args:
        message_id: Gmail message ID
        label: Label name to add

    Returns:
        Result with success status and label details
    """
    client = _get_client()

    # Get or create label
    labels = await client.list_labels()
    label_map = {l["name"]: l["id"] for l in labels}

    if label in label_map:
        label_id = label_map[label]
        created = False
    else:
        # Create the label
        new_label = await client.create_label(label)
        label_id = new_label["id"]
        created = True

    # Add label to message
    success = await client.add_label(message_id, label_id)

    return {
        "success": success,
        "message_id": message_id,
        "label": label,
        "label_id": label_id,
        "label_created": created,
    }


@mcp.tool()
async def remove_label(message_id: str, label: str) -> dict:
    """Remove a label from an email.

    Args:
        message_id: Gmail message ID
        label: Label name to remove

    Returns:
        Result with success status
    """
    client = _get_client()

    # Find label ID
    labels = await client.list_labels()
    label_map = {l["name"]: l["id"] for l in labels}

    if label not in label_map:
        return {
            "success": False,
            "error": f"Label '{label}' not found",
            "message_id": message_id,
        }

    label_id = label_map[label]
    success = await client.remove_label(message_id, label_id)

    return {
        "success": success,
        "message_id": message_id,
        "label": label,
        "label_id": label_id,
    }


@mcp.tool()
async def archive_email(message_id: str) -> dict:
    """Archive an email (remove from INBOX).

    Args:
        message_id: Gmail message ID

    Returns:
        Result with success status
    """
    client = _get_client()
    success = await client.archive(message_id)

    return {
        "success": success,
        "message_id": message_id,
        "archived": success,
    }


# =============================================================================
# Entry Point
# =============================================================================


def main():
    """Run the Gmail MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
