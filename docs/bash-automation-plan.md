# Bash Automation Feature — Implementation Plan

> **Status:** Planning
> **Created:** 2026-01-28
> **Branch:** `claude/plan-bash-automation-P7Ybx`

## Overview

Enable PAI to run custom bash scripts as automations, with LLM-generated scripts sandboxed and executed securely. This integrates with the intent engine so users can describe what they want ("compress logs and upload to S3") and PAI generates and runs the appropriate script.

---

## Design Decisions

### Security & Sandboxing

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Sandbox model | Strong sandbox via `bubblewrap` or `firejail` | LLM generates scripts; must assume untrusted code |
| Approval flow | First-run approval, then auto-run | Balance security with usability |
| Capability enforcement | Declared paths/commands enforced by sandbox | Defense in depth |

### Script Definition

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | Path-based — `~/.config/pai/scripts/{uuid}.sh` | Auditable, versionable, inspectable |
| Directory structure | Flat with UUID filenames | Simple, avoids path traversal issues |
| Variable injection | `${variable.path}` in script body | Consistent with existing action template syntax |
| Injection safety | Shell-escape all values before substitution | Prevent command injection |

### Execution

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Timeout | Default 60s, soft warn, hard kill at 30min | Prevent runaway scripts |
| Output capture | Separate stdout/stderr, 10KB each | Debugging without storage bloat |
| Exit codes | `0`=success, `1-127`=soft fail (continue), `128+`=hard fail (stop) | Match Unix signal conventions |

### Architecture

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Implementation | MCP server at `pai_mcp/bash.py` | Consistent with MCP-only connector pattern |
| Intent integration | Full — reference existing or generate new scripts | Core value prop for LLM-driven automation |

---

## Data Models

### New Action Type: `BashAction`

Add to `models.py`:

```python
class BashCapability(BaseModel):
    """Declared capabilities for sandbox enforcement."""
    paths_read: list[str] = []      # e.g., ["/home/user/logs", "/tmp"]
    paths_write: list[str] = []     # e.g., ["/tmp/output"]
    network: bool = False           # Allow network access
    commands: list[str] = []        # Allowed commands, e.g., ["tar", "gzip", "curl"]


class BashAction(BaseModel):
    """Execute a sandboxed bash script."""
    type: Literal["bash.run"] = "bash.run"

    # Script reference (UUID filename in ~/.config/pai/scripts/)
    script_id: str

    # Human-readable name for intent engine references
    script_name: str | None = None

    # Declared capabilities (enforced by sandbox)
    capabilities: BashCapability

    # Execution settings
    timeout_seconds: int = 60                    # Default 60s
    timeout_hard_seconds: int = 1800             # Hard limit 30min
    working_directory: str | None = None         # Defaults to script dir

    # Variables to inject (resolved from automation variables)
    variables: dict[str, str] = {}               # {"LOG_PATH": "${trigger.file.path}"}

    # Approval tracking
    approved: bool = False                       # Set True after first-run approval
    approved_at: datetime | None = None
    approved_hash: str | None = None             # SHA256 of approved script content
```

### Script Metadata

Store alongside scripts or in DB:

```python
class ScriptMetadata(BaseModel):
    """Metadata for a managed script."""
    id: str                          # UUID
    name: str                        # Human-readable name
    description: str                 # What the script does
    created_at: datetime
    created_by: Literal["llm", "user"]
    content_hash: str                # SHA256 for integrity/approval tracking
    automation_ids: list[str] = []   # Automations using this script
```

---

## MCP Server: `pai_mcp/bash.py`

### Tools

| Tool | Description |
|------|-------------|
| `bash.run_script` | Execute a script by ID with sandboxing |
| `bash.create_script` | Write a new script to managed directory |
| `bash.list_scripts` | List available scripts with metadata |
| `bash.get_script` | Read script content by ID |
| `bash.delete_script` | Remove a script |
| `bash.validate_script` | Syntax check + static analysis (shellcheck) |

### `bash.run_script` Input Schema

```json
{
  "script_id": "string (required)",
  "variables": "object (optional) - key-value pairs to inject",
  "capabilities": {
    "paths_read": ["string"],
    "paths_write": ["string"],
    "network": "boolean",
    "commands": ["string"]
  },
  "timeout_seconds": "integer (default: 60)",
  "dry_run": "boolean (default: false)"
}
```

### `bash.run_script` Output Schema

```json
{
  "success": "boolean",
  "exit_code": "integer",
  "exit_status": "success | soft_failure | hard_failure",
  "stdout": "string (truncated to 10KB)",
  "stderr": "string (truncated to 10KB)",
  "duration_ms": "integer",
  "timed_out": "boolean",
  "sandbox_violations": ["string"]
}
```

---

## Sandbox Implementation

### Bubblewrap Configuration

```python
def build_sandbox_command(
    script_path: str,
    capabilities: BashCapability,
    working_dir: str,
) -> list[str]:
    """Build bwrap command with capability-based restrictions."""
    cmd = [
        "bwrap",
        "--die-with-parent",
        "--unshare-all",
        "--share-net" if capabilities.network else "--unshare-net",

        # Mount minimal filesystem
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--symlink", "/usr/bin", "/bin",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]

    # Add declared read paths
    for path in capabilities.paths_read:
        cmd.extend(["--ro-bind", path, path])

    # Add declared write paths
    for path in capabilities.paths_write:
        cmd.extend(["--bind", path, path])

    # Set working directory
    cmd.extend(["--chdir", working_dir])

    # Execute script
    cmd.extend(["bash", script_path])

    return cmd
```

### Fallback: Firejail

If `bwrap` unavailable, use `firejail` with similar restrictions:

```bash
firejail --quiet --noprofile \
    --net=none \
    --whitelist=/path/to/read \
    --read-write=/path/to/write \
    bash /path/to/script.sh
```

---

## Variable Injection

### Shell-Escaping Implementation

```python
import shlex

def resolve_script_variables(
    script_content: str,
    variables: dict[str, Any],
) -> str:
    """Resolve ${var.path} templates with shell-escaped values."""

    def get_nested_value(data: dict, path: str) -> str | None:
        """Traverse nested dict with dot notation."""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return str(current) if current is not None else None

    def replace_var(match: re.Match) -> str:
        path = match.group(1)
        value = get_nested_value(variables, path)
        if value is None:
            return match.group(0)  # Keep original if not found
        return shlex.quote(value)  # Shell-escape!

    pattern = r"\$\{([^}]+)\}"
    return re.sub(pattern, replace_var, script_content)
```

### Example

**Script template:**
```bash
#!/bin/bash
tar -czf /tmp/backup.tar.gz ${trigger.file.path}
echo "Backed up: ${trigger.file.path}"
```

**Variables:**
```python
{"trigger": {"file": {"path": "/home/user/my file with spaces.txt"}}}
```

**Resolved script:**
```bash
#!/bin/bash
tar -czf /tmp/backup.tar.gz '/home/user/my file with spaces.txt'
echo "Backed up: '/home/user/my file with spaces.txt'"
```

---

## First-Run Approval Flow

### Approval Process

1. **Action created** — `BashAction.approved = False`
2. **First execution triggered** — Executor detects `approved = False`
3. **Prompt user** — Show script content, capabilities, and ask for approval
4. **User approves** — Set `approved = True`, `approved_hash = sha256(content)`
5. **Subsequent runs** — Check `content_hash == approved_hash`; if mismatch, re-prompt

### Hash Verification

```python
def verify_script_approval(action: BashAction, script_content: str) -> bool:
    """Check if script still matches approved version."""
    if not action.approved:
        return False
    current_hash = hashlib.sha256(script_content.encode()).hexdigest()
    return current_hash == action.approved_hash
```

---

## Intent Engine Integration

### Referencing Existing Scripts

User: *"When I get an email from backup@cron.daily, run my log-cleanup script"*

Intent engine matches "log-cleanup script" to existing script by name:

```python
# In intent.py planning phase
{
    "type": "bash.run",
    "script_id": "a1b2c3d4-...",
    "script_name": "log-cleanup",
    "capabilities": {
        "paths_read": ["/var/log"],
        "paths_write": ["/var/log"],
        "commands": ["find", "rm", "gzip"]
    }
}
```

### Generating New Scripts

User: *"When a file is added to ~/invoices, extract the total and append to my expenses spreadsheet"*

Intent engine generates script via LLM:

1. **Plan phase** — LLM generates script content based on description
2. **Create script** — Call `bash.create_script` via MCP
3. **Build action** — Reference new script ID in `BashAction`
4. **Require approval** — `approved = False` triggers first-run prompt

### LLM Script Generation Prompt Template

```
Generate a bash script for the following task:
{user_description}

Requirements:
- Use only these commands: {allowed_commands}
- Read from: {paths_read}
- Write to: {paths_write}
- Network access: {network_allowed}

The script will receive these variables (shell-escaped):
{available_variables}

Output only the script content, no explanations.
```

---

## MCP Routing Update

Add to `mcp.py`:

```python
ACTION_TO_MCP_TOOL = {
    # ... existing mappings ...

    # Bash
    "bash.run": ("bash", "run_script"),
}
```

---

## Implementation Phases

### Phase 1: Core Infrastructure
- [ ] Add `BashAction` and `BashCapability` to `models.py`
- [ ] Add `ScriptMetadata` model
- [ ] Create `pai_mcp/bash.py` MCP server skeleton
- [ ] Implement `bash.create_script` and `bash.get_script` tools
- [ ] Implement script storage in `~/.config/pai/scripts/`

### Phase 2: Sandbox Execution
- [ ] Implement `bubblewrap` sandbox builder
- [ ] Implement `firejail` fallback
- [ ] Add sandbox detection (which tool is available)
- [ ] Implement `bash.run_script` with sandboxing
- [ ] Add variable injection with shell-escaping
- [ ] Add timeout handling (soft warn + hard kill)
- [ ] Add stdout/stderr capture with truncation

### Phase 3: Approval Flow
- [ ] Add approval fields to `BashAction`
- [ ] Implement hash verification
- [ ] Add approval prompt in executor
- [ ] Store approval state in database

### Phase 4: Intent Integration
- [ ] Add script name matching in intent engine
- [ ] Add LLM script generation in planning phase
- [ ] Add capability inference from task description
- [ ] Update `bash.validate_script` with shellcheck

### Phase 5: Polish
- [ ] Add `bash.list_scripts` and `bash.delete_script` tools
- [ ] Add CLI commands: `pai scripts list`, `pai scripts show <id>`
- [ ] Add dry-run support showing resolved script
- [ ] Add execution logging and audit trail

---

## Testing Strategy

### Unit Tests

- Variable injection with special characters
- Shell-escaping edge cases (quotes, newlines, unicode)
- Exit code classification (0, 1-127, 128+)
- Timeout handling
- Hash verification

### Integration Tests

- Full sandbox execution with bubblewrap
- Capability enforcement (attempt to read disallowed path)
- First-run approval flow
- Script modification detection

### Security Tests

- Command injection via variable injection
- Path traversal attempts
- Sandbox escape attempts
- Resource exhaustion (fork bomb, memory)

---

## Open Questions

1. **Shellcheck integration** — Run static analysis before approval? Warn on issues?
2. **Script versioning** — Keep old versions when LLM regenerates? How many?
3. **Shared scripts** — Allow scripts to be shared across automations, or one-to-one?
4. **Execution environment** — Minimal PATH? Custom env vars beyond variables?

---

## Dependencies

| Dependency | Purpose | Required |
|------------|---------|----------|
| `bubblewrap` | Primary sandbox | Yes (or firejail) |
| `firejail` | Fallback sandbox | Optional |
| `shellcheck` | Script validation | Optional |

System packages, not Python dependencies.

---

## References

- [Bubblewrap docs](https://github.com/containers/bubblewrap)
- [Firejail docs](https://firejail.wordpress.com/)
- Existing MCP pattern: `pai_mcp/gmail.py`
- Variable resolution: `executor.py:208-245`
