# PAI - Personal AI OS

## Quick Reference
- **Spec:** `personal-ai-os-spec.md` - Full system design
- **Patterns:** `docs/patterns.md` - Code patterns to follow

## Development Principles

### AI-Accelerated Development
1. **Flat structure** - One level of nesting max, split files only when >300 lines
2. **CLI first** - Build core logic before any UI
3. **Reference, don't explain** - Point to spec or existing code
4. **Sequential depth** - One phase at a time

### Code Style
- Follow the Zen of Python (PEP 20)
- Pydantic for all data models
- Type hints everywhere
- Async by default for I/O operations

### Key Patterns
- **LLM calls:** Use `llm.py` providers, never raw API calls
- **Database:** Use `db.py` wrapper, never raw SQL outside it
- **Config:** Use Pydantic Settings, read from `~/.config/pai/`
- **CLI:** Use Typer with Rich for output
- **Connectors:** Use MCP servers in `pai_mcp/`, never direct API calls in executor

### File Responsibilities
| File | Purpose |
|------|---------|
| `models.py` | Pydantic models: Automation, Entity, Execution, etc. |
| `db.py` | SQLite wrapper, schema, migrations |
| `llm.py` | LLM providers (Claude, llama.cpp) |
| `intent.py` | Intent Engine: parse → clarify → plan |
| `executor.py` | Execution Engine: run automations via MCP |
| `mcp.py` | MCP client manager: connect to MCP servers, route actions |
| `cli.py` | Typer commands |
| `config.py` | Settings management |
| `gmail.py` | Gmail client (used by MCP server) |

### MCP Servers (`pai_mcp/`)
| Server | Purpose |
|--------|---------|
| `gmail/server.py` | Gmail MCP server: search, label, archive emails |

### Commands
```bash
uv run pai --help          # Run CLI
uv run pytest              # Run tests
uv run pai intent "..."    # Parse natural language intent
```

### Don'ts
- Don't create new files without asking
- Don't add dependencies without asking
- Don't nest folders deeper than `src/pai/` or `pai_mcp/<server>/`
- Don't write documentation files unless asked
- Don't add legacy/fallback code paths - MCP-only for connectors
