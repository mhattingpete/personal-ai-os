# PAI Roadmap

Personal AI OS - automate your digital life with natural language.

## Completed

- **Foundation** - CLI, SQLite database, Pydantic models, configuration
- **Intent Engine** - Natural language → structured automation specs
- **Gmail Connector** - OAuth, search, labels, entity extraction
- **Execution Engine** - Run automations, dry-run mode, history
- **Trigger System** - Email watcher with condition matching
- **MCP Architecture** - Model Context Protocol for all connectors (`pai_mcp/`)
- **Outlook Connector** - Email + calendar via Microsoft Graph (external MCP)

## Short-Term

Priority: Enable the 3 MVP workflows from the spec.

| Feature | Purpose |
|---------|---------|
| **Google Sheets MCP** | Log extracted data to spreadsheets (`pai_mcp/sheets.py`) |
| **Google Drive MCP** | Save attachments to organized folders (`pai_mcp/drive.py`) |
| **Attachment Executor** | Download and save email attachments |
| **Schedule Triggers** | Run automations on cron schedules |
| **Approval Gates** | Require confirmation for high-stakes actions |

## Medium-Term

Priority: Polish, learn, expand.

| Feature | Purpose |
|---------|---------|
| **Learn Stage** | Capture corrections, auto-refine automations |
| **Semantic Triggers** | LLM-powered condition matching ("invoice-related") |
| **More MCP Servers** | Notion, Slack, Google Calendar |
| **Cost Tracking** | Monitor API usage and LLM costs |
| **Error Recovery** | Retry logic, partial rollback support |

## Long-Term

Priority: Advanced capabilities and scale.

| Feature | Purpose |
|---------|---------|
| **Local Semantic Index** | Embeddings for cross-source queries |
| **Code Sandbox** | Safe execution of LLM-generated code |
| **Sync Layer** | CRDT-based state across devices |
| **Visual Canvas** | Drag-drop workflow builder UI |
| **Community Library** | Share and fork automations |
| **Mobile Companion** | Approve actions on the go |

## MVP Workflows

These 3 workflows drive prioritization:

1. **Client Email Triage** - Categorize emails, extract deadlines *(working)*
2. **Invoice Processing** - PDF → folder → spreadsheet *(needs Drive + Sheets)*
3. **Weekly Client Report** - Aggregate activity per client *(needs Schedule)*

## Architecture Principles

- **Local-first** - Your data stays on your machine
- **CLI-first** - Core logic before UI
- **Flat structure** - Easy to navigate and extend
- **Async throughout** - Ready for I/O-heavy workloads
- **MCP-based** - All connectors are MCP servers in `pai_mcp/`
- **Pluggable** - Connectors and LLM providers are extensible
