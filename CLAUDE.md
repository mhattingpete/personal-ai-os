# PAI - Personal AI OS

## Quick Reference
- **Spec:** `personal-ai-os-spec.md` - Full system design
- **Roadmap:** `ROADMAP.md` - Feature roadmap and progress
- **Python:** >=3.12 required
- **Package manager:** `uv`

## Project Structure
```
src/pai/          # Main application (12 modules)
pai_mcp/          # MCP server implementations
tests/            # pytest test suite
```

## Development Principles

### AI-Accelerated Development
1. **Flat structure** - One level of nesting max, split files only when >300 lines
2. **CLI first** - Build core logic before any UI
3. **Reference, don't explain** - Point to spec or existing code
4. **Sequential depth** - One phase at a time

### Code Style
- Follow the Zen of Python (PEP 20)
- Pydantic v2 for all data models
- Type hints everywhere
- Async by default for I/O operations
- Line length: 100 (ruff)

### Key Patterns
- **LLM calls:** Use `llm.py` providers, never raw API calls
- **Database:** Use `db.py` wrapper, never raw SQL outside it
- **Config:** Use Pydantic Settings, read from `~/.config/pai/`
- **CLI:** Use Typer with Rich for output
- **Connectors:** Use MCP servers in `pai_mcp/`, never direct API calls in executor
- **Singletons:** `get_db()`, `get_settings()`, `get_mcp_manager()` for global instances

### File Responsibilities
| File | Purpose |
|------|---------|
| `models.py` | Pydantic models: Automation, Entity, Execution, Trigger, Action, etc. |
| `db.py` | Async SQLite wrapper (aiosqlite), schema v2, migrations |
| `llm.py` | LLM providers (Claude, llama.cpp) with routing and structured outputs |
| `intent.py` | Intent Engine: parse → clarify → plan pipeline |
| `executor.py` | Execution Engine: run automations via MCP, LLM email classification |
| `mcp.py` | MCP client manager: connect to MCP servers, route actions |
| `cli.py` | Typer commands: init, config, status, list, intent, connect, emails, entities, mcp |
| `config.py` | Pydantic Settings with YAML + env var support |
| `gmail.py` | Gmail OAuth client: search, labels, entity extraction |
| `watcher.py` | Email trigger watcher: polls for new emails, evaluates conditions |

### MCP Servers (`pai_mcp/`)
| Server | Purpose |
|--------|---------|
| `gmail.py` | Gmail MCP server (FastMCP): search, get, label, archive emails |

### External MCP Servers (configured in `~/.config/pai/mcp.json`)
| Server | Repo | Purpose |
|--------|------|---------|
| `outlook` | `outlook-mcp` | Outlook emails + calendar via Microsoft Graph |

### Commands
```bash
uv run pai --help          # Run CLI
uv run pytest              # Run tests
uv run pai intent "..."    # Parse natural language intent
uv run pai init            # Initialize config + database
uv run pai connect google  # OAuth connect to Google
uv run pai emails "query"  # Search emails
uv run pai entities        # List discovered entities
uv run pai mcp list        # List MCP servers
```

### Architecture Flow
```
User (CLI) → Intent Engine → Automation spec
                                   ↓
Watcher (poll triggers) → Executor → MCP Manager → MCP Servers → External APIs
                              ↓
                          Database (results, state)
```

### Configuration Paths
- **Config:** `~/.config/pai/config.yaml` - LLM settings, routing, debug
- **MCP:** `~/.config/pai/mcp.json` - MCP server definitions
- **Data:** `~/.local/share/pai/pai.db` - SQLite database
- **Gmail:** `~/.config/pai/google_credentials.json` + `google_token.json`

### Don'ts
- Don't create new files without asking
- Don't add dependencies without asking
- Don't nest folders deeper than `src/pai/` or `pai_mcp/`
- Don't write documentation files unless asked
- Don't add legacy/fallback code paths - MCP-only for connectors
