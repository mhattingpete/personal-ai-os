# PAI - Personal AI OS

Automate your digital life with natural language. PAI lets you create automations by describing what you want in plain English.

## Installation

```bash
# Clone and install
git clone https://github.com/mhattingpete/personal-ai-os.git
cd personal-ai-os
uv sync
```

## Quick Start

```bash
# Initialize PAI (creates config and database)
uv run pai init

# Parse a natural language intent
uv run pai intent "Label emails from clients as Important"

# Connect to Gmail
uv run pai connect google

# Search your emails
uv run pai emails "has:attachment newer_than:7d"

# Discover clients from your emails
uv run pai entities --discover
```

## Commands

### `pai init`

Initialize PAI - creates configuration directory and database.

```bash
uv run pai init
```

### `pai intent`

Parse natural language into automation specs. This is the core of PAI - describe what you want and it figures out the trigger, conditions, and actions.

```bash
# Basic usage
uv run pai intent "When a client emails me with an invoice, save it to their folder"

# Generate full automation spec
uv run pai intent "Label emails from acme.com as Client" --plan

# Use local LLM (llama.cpp) instead of Claude
uv run pai intent "Archive newsletters older than 30 days" --local

# Save the automation to database
uv run pai intent "Forward urgent emails to Slack" --plan --save
```

**Options:**
- `--plan, -p` - Generate full automation specification
- `--save, -s` - Save automation to database
- `--local, -l` - Use local LLM (requires llama.cpp server)

### `pai connect`

Connect to external services via OAuth.

```bash
# Connect to Gmail
uv run pai connect google

# Force re-authentication
uv run pai connect google --force
```

**Gmail Setup:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create a new project (or select existing)
3. Enable the Gmail API
4. Create OAuth 2.0 Client ID (Desktop application)
5. Download the JSON file
6. Save as `~/.config/pai/gmail_credentials.json`
7. Run `pai connect google`

### `pai emails`

Search emails using Gmail query syntax.

```bash
# Search by sender
uv run pai emails "from:client@example.com"

# Search with attachments
uv run pai emails "has:attachment filename:pdf"

# Recent emails with keyword
uv run pai emails "newer_than:7d subject:invoice"

# Show more results
uv run pai emails "is:unread" --max 20

# Include email body in output
uv run pai emails "from:boss@company.com" --body
```

**Options:**
- `--max, -m` - Maximum results (default: 10)
- `--body, -b` - Show email body preview

**Common Gmail Query Operators:**
- `from:` - Sender email or name
- `to:` - Recipient
- `subject:` - Subject line contains
- `has:attachment` - Has attachments
- `filename:` - Attachment filename
- `newer_than:Nd` - Within last N days
- `older_than:Nd` - Older than N days
- `is:unread` - Unread emails
- `label:` - Has specific label

### `pai entities`

Manage entities (clients, people, projects) discovered from your data.

```bash
# List all entities
uv run pai entities

# Filter by type
uv run pai entities --type client
uv run pai entities --type person

# Discover entities from recent emails
uv run pai entities --discover

# Limit results
uv run pai entities --limit 50
```

**Options:**
- `--type, -t` - Filter by type (client, person, project)
- `--discover, -d` - Scan recent emails to find new entities
- `--limit, -l` - Maximum results (default: 20)

### `pai list`

List all automations.

```bash
# List all
uv run pai list

# Filter by status
uv run pai list --status active
uv run pai list --status draft
```

### `pai status`

Show PAI status (database, automations, connectors).

```bash
uv run pai status
```

### `pai config`

View configuration.

```bash
# Show config directory
uv run pai config

# Show all settings
uv run pai config --show
```

## Configuration

PAI stores configuration in `~/.config/pai/`:

```
~/.config/pai/
├── config.yaml           # Settings (optional)
├── gmail_credentials.json # Gmail OAuth client (from Google)
└── gmail_token.json      # Gmail OAuth token (auto-generated)
```

### Environment Variables

- `ANTHROPIC_API_KEY` - Claude API key (required for cloud LLM)

### config.yaml (optional)

```yaml
llm:
  default: claude  # or "local"
  claude:
    model: claude-sonnet-4-20250514
  local:
    url: http://localhost:8080
  routing:
    force_local: false
    sensitive_domains:
      - health
      - finance

debug: false
```

## Using Local LLM

PAI supports local LLMs via llama.cpp's OpenAI-compatible API.

1. Start llama.cpp server:
   ```bash
   ./llama-server -m your-model.gguf --port 8080
   ```

2. Use `--local` flag:
   ```bash
   uv run pai intent "Label emails from clients" --local
   ```

## Examples

### Email Automation Workflow

```bash
# 1. Connect Gmail
uv run pai connect google

# 2. Discover your clients
uv run pai entities --discover

# 3. Create automation
uv run pai intent "When acme.com emails me with an invoice PDF, label it as 'Acme Invoice'" --plan

# 4. Check the automation
uv run pai list
```

### Search and Organize

```bash
# Find invoices from last month
uv run pai emails "subject:invoice newer_than:30d has:attachment"

# Find unread from specific sender
uv run pai emails "from:important@client.com is:unread"
```

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Run specific test file
uv run pytest tests/test_gmail.py -v
```

## License

MIT
