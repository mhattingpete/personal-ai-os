"""CLI entry point for PAI.

Uses Typer for command parsing and Rich for output.
Run with: uv run pai --help
"""

import asyncio
from functools import wraps
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pai.config import get_config_dir, get_data_dir, get_settings
from pai.db import get_db, init_db
from pai.models import AutomationStatus, ExecutionStatus, TriggerEvent

app = typer.Typer(
    name="pai",
    help="Personal AI OS - automate your digital life with natural language",
    no_args_is_help=True,
)
console = Console()


def async_command(f):
    """Decorator to run async functions as CLI commands."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@app.callback()
def callback(
    debug: Annotated[
        bool, typer.Option("--debug", "-d", help="Enable debug output")
    ] = False,
):
    """PAI - Personal AI Operating System."""
    if debug:
        console.print("[dim]Debug mode enabled[/dim]")


# =============================================================================
# Setup subgroup (init, config, connect, status as default)
# =============================================================================

setup_app = typer.Typer(help="Setup and configure PAI")
app.add_typer(setup_app, name="setup")


@setup_app.callback(invoke_without_command=True)
@async_command
async def setup_status(ctx: typer.Context):
    """Show PAI status (default when no subcommand)."""
    if ctx.invoked_subcommand is not None:
        return

    db = get_db()
    try:
        await db.initialize()
        automations = await db.list_automations()
        active_count = len([a for a in automations if a.status == AutomationStatus.ACTIVE])
        connectors = await db.list_connectors()

        console.print(Panel.fit(
            f"[bold]Automations:[/bold] {len(automations)} total, {active_count} active\n"
            f"[bold]Connectors:[/bold] {len(connectors)} connected",
            title="PAI Status",
        ))
    finally:
        await db.close()


@setup_app.command("init")
@async_command
async def setup_init():
    """Initialize PAI (create config and database)."""
    console.print(Panel.fit(
        f"[bold]Config:[/bold] {get_config_dir()}\n"
        f"[bold]Data:[/bold] {get_data_dir()}",
        title="PAI Directories",
    ))

    db = await init_db()
    console.print("[green]✓[/green] Database initialized")

    settings = get_settings()
    if settings.llm.claude.api_key:
        console.print("[green]✓[/green] Claude API key configured")
    else:
        console.print(
            "[yellow]![/yellow] Claude API key not set. "
            "Set ANTHROPIC_API_KEY or add to ~/.config/pai/config.yaml"
        )

    await db.close()
    console.print("\n[bold green]PAI initialized![/bold green]")


@setup_app.command("config")
def setup_config(
    show: Annotated[
        bool, typer.Option("--show", "-s", help="Show current configuration")
    ] = False,
):
    """Manage PAI configuration."""
    settings = get_settings()

    if show:
        table = Table(title="PAI Configuration")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("LLM Default", settings.llm.default)
        table.add_row("Claude Model", settings.llm.claude.model)
        table.add_row(
            "Claude API Key",
            "***" + settings.llm.claude.api_key[-4:]
            if settings.llm.claude.api_key
            else "[red]Not set[/red]",
        )
        table.add_row("Local LLM URL", settings.llm.local.url)
        table.add_row("Database Path", str(settings.db.path))
        table.add_row("Debug Mode", str(settings.debug))

        console.print(table)
    else:
        console.print(f"Config directory: {get_config_dir()}")
        console.print("Use --show to display current configuration")


@setup_app.command("connect")
@async_command
async def setup_connect(
    service: Annotated[str, typer.Argument(help="Service to connect (google, gmail)")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Force re-authentication")] = False,
):
    """Connect to an external service (currently: google/gmail)."""
    if service.lower() not in ("google", "gmail"):
        console.print(f"[yellow]Connector for '{service}' not yet implemented[/yellow]")
        console.print("[dim]Supported: google, gmail[/dim]")
        return

    from pai.gmail import get_gmail_client

    console.print(Panel.fit(
        "[bold]Connecting to Google/Gmail[/bold]\n\n"
        "This will open a browser window for OAuth authentication.\n"
        "Make sure you have gmail_credentials.json in ~/.config/pai/",
        title="Gmail OAuth",
    ))

    try:
        client = get_gmail_client()
        connector = await client.authenticate(force_refresh=force)

        db = get_db()
        await db.initialize()
        await db.save_connector(connector)
        await db.close()

        console.print(f"\n[green]Connected to Gmail as:[/green] {connector.account_id}")
        labels = await client.list_labels()
        user_labels = [l for l in labels if l.get("type") != "system"]
        if user_labels:
            console.print(f"[dim]Found {len(user_labels)} user labels[/dim]")

    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("\n[yellow]To set up Gmail API:[/yellow]")
        console.print("1. Go to https://console.cloud.google.com/apis/credentials")
        console.print("2. Create OAuth 2.0 Client ID (Desktop app)")
        console.print("3. Download JSON and save as ~/.config/pai/gmail_credentials.json")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")


# =============================================================================
# Core workflow commands (intent, run, list, watch)
# =============================================================================


@app.command()
@async_command
async def intent(
    text: Annotated[str, typer.Argument(help="Natural language intent to parse")],
    plan: Annotated[bool, typer.Option("--plan", "-p", help="Generate full automation spec")] = False,
    save: Annotated[bool, typer.Option("--save", "-s", help="Save the automation to database")] = False,
    local: Annotated[bool, typer.Option("--local", "-l", help="Use local LLM (llama.cpp)")] = False,
):
    """Parse a natural language intent into an automation spec."""
    import yaml
    from rich.syntax import Syntax

    from pai.intent import IntentEngine
    from pai.llm import get_provider

    console.print(Panel.fit(f"[bold]Input:[/bold] {text}", title="Intent Parsing"))

    provider = get_provider("local" if local else "claude")
    if local:
        console.print("[dim]Using local LLM (llama.cpp)[/dim]\n")

    engine = IntentEngine(provider)

    with console.status("[bold blue]Parsing intent...[/bold blue]"):
        result = await engine.parse(text)

    intent_graph = result.intent

    # Display parsed intent
    console.print("\n[bold cyan]Parsed Intent[/bold cyan]")
    console.print(f"  Type: [green]{intent_graph.type}[/green]")
    console.print(f"  Confidence: [yellow]{intent_graph.confidence:.0%}[/yellow]")

    if intent_graph.trigger:
        console.print(f"  Trigger: [blue]{intent_graph.trigger.type}[/blue]")
        for cond in intent_graph.trigger.conditions:
            console.print(f"    - {cond}")

    for action in intent_graph.actions:
        console.print(f"  Actions:\n    - [magenta]{action.type}[/magenta] (confidence: {action.confidence:.0%})")
        for k, v in action.params.items():
            console.print(f"      {k}: {v}")

    if intent_graph.ambiguities:
        console.print("\n[bold yellow]Ambiguities Detected[/bold yellow]")
        for amb in intent_graph.ambiguities:
            console.print(f"  [yellow]?[/yellow] {amb.description}")
            for q in amb.suggested_questions:
                console.print(f"    - {q}")

    if result.needs_clarification:
        console.print("\n[dim]Run with --plan after clarifying ambiguities[/dim]")
        with console.status("[bold blue]Generating questions...[/bold blue]"):
            clarify_result = await engine.clarify(intent_graph)

        if clarify_result.questions:
            console.print("\n[bold cyan]Clarification Questions[/bold cyan]")
            for i, q in enumerate(clarify_result.questions, 1):
                console.print(f"\n  {i}. {q.question}")
                for opt in q.options:
                    default = " [dim](default)[/dim]" if opt == q.default else ""
                    console.print(f"     - {opt}{default}")
        return

    if plan or result.ready_for_planning:
        console.print("\n[bold cyan]Generating Automation Spec...[/bold cyan]")
        with console.status("[bold blue]Planning automation...[/bold blue]"):
            plan_result = await engine.plan(intent_graph)

        automation = plan_result.automation
        console.print(f"\n[bold green]Automation: {automation.name}[/bold green]")
        console.print(f"  {plan_result.summary}")

        auto_dict = {
            "name": automation.name,
            "description": automation.description,
            "trigger": {"type": automation.trigger.type},
            "actions": [{"type": a.type} for a in automation.actions],
        }
        console.print("\n[bold]Automation Spec:[/bold]")
        console.print(Syntax(yaml.dump(auto_dict, default_flow_style=False, sort_keys=False), "yaml", theme="monokai"))

        if plan_result.warnings:
            console.print("\n[bold yellow]Warnings:[/bold yellow]")
            for w in plan_result.warnings:
                console.print(f"  [yellow]![/yellow] {w}")

        if save:
            db = get_db()
            await db.initialize()
            await db.save_automation(automation)
            await db.close()
            console.print(f"\n[green]Saved automation:[/green] {automation.id}")


@app.command("run")
@async_command
async def run_automation_cmd(
    automation_id: Annotated[str, typer.Argument(help="Automation ID to run")],
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Simulate without executing")] = False,
    local: Annotated[bool, typer.Option("--local", "-l", help="Use local LLM (llama.cpp)")] = False,
    email_id: Annotated[Optional[str], typer.Option("--email", "-e", help="Email message ID for trigger")] = None,
):
    """Run an automation manually."""
    from pai.executor import run_automation

    mode = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]EXECUTING[/green]"
    console.print(f"\n{mode} automation: [cyan]{automation_id}[/cyan]\n")
    if local:
        console.print("[dim]Using local LLM (llama.cpp)[/dim]\n")

    trigger_event = None
    if email_id:
        trigger_event = TriggerEvent(type="email", data={"email": {"id": email_id}})
        console.print(f"[dim]Using email: {email_id}[/dim]\n")

    try:
        execution = await run_automation(
            automation_id,
            trigger_event=trigger_event,
            dry_run=dry_run,
            provider_name="local" if local else None,
        )

        status_style = {
            "success": "[green]SUCCESS[/green]",
            "failed": "[red]FAILED[/red]",
            "partial": "[yellow]PARTIAL[/yellow]",
            "running": "[blue]RUNNING[/blue]",
        }.get(execution.status.value, str(execution.status))

        console.print(f"Status: {status_style}")
        console.print(f"Execution ID: [dim]{execution.id}[/dim]")

        if execution.action_results:
            console.print("\n[bold]Actions:[/bold]")
            for result in execution.action_results:
                icon = "[green]✓[/green]" if result.status == "success" else "[red]✗[/red]" if result.status == "failed" else "[yellow]○[/yellow]"
                console.print(f"  {icon} {result.action_id}")
                if result.output and not dry_run:
                    console.print(f"      [dim]{result.output}[/dim]")
                if result.error:
                    console.print(f"      [red]Error: {result.error}[/red]")

        if execution.error:
            console.print(f"\n[red]Error:[/red] {execution.error.message}")

    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")


@app.command("list")
@async_command
async def list_automations(
    status: Annotated[
        Optional[str],
        typer.Option("--status", "-s", help="Filter by status (draft, active, paused, error)"),
    ] = None,
    activate: Annotated[
        Optional[str],
        typer.Option("--activate", "-a", help="Activate automation by ID"),
    ] = None,
    pause: Annotated[
        Optional[str],
        typer.Option("--pause", "-p", help="Pause automation by ID"),
    ] = None,
    delete: Annotated[
        Optional[str],
        typer.Option("--delete", help="Delete automation by ID"),
    ] = None,
    history: Annotated[
        bool,
        typer.Option("--history", "-h", help="Show execution history"),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Limit results (for history)"),
    ] = 20,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation (for delete)"),
    ] = False,
):
    """List automations, or manage them with flags.

    Examples:
        pai list                   # List all automations
        pai list --status active   # Filter by status
        pai list --activate <id>   # Activate an automation
        pai list --pause <id>      # Pause an automation
        pai list --delete <id>     # Delete an automation
        pai list --history         # Show execution history
    """
    db = get_db()
    await db.initialize()

    try:
        # Handle --activate
        if activate:
            from pai.executor import activate_automation
            try:
                automation = await activate_automation(activate)
                console.print(f"[green]Activated:[/green] {automation.name} ({automation.id})")
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
            return

        # Handle --pause
        if pause:
            from pai.executor import pause_automation
            try:
                automation = await pause_automation(pause)
                console.print(f"[yellow]Paused:[/yellow] {automation.name} ({automation.id})")
            except ValueError as e:
                console.print(f"[red]Error:[/red] {e}")
            return

        # Handle --delete
        if delete:
            automation = await db.get_automation(delete)
            if not automation:
                console.print(f"[red]Error:[/red] Automation not found: {delete}")
                return

            if not force and not typer.confirm(f"Delete automation '{automation.name}'?"):
                console.print("[dim]Cancelled[/dim]")
                return

            deleted = await db.delete_automation(delete)
            console.print(f"[green]Deleted:[/green] {automation.name}" if deleted else "[red]Error:[/red] Failed to delete")
            return

        # Handle --history
        if history:
            executions = await db.list_executions(limit=limit)

            if not executions:
                console.print("[dim]No executions found[/dim]")
                return

            table = Table(title="Execution History")
            table.add_column("ID", style="cyan", max_width=16)
            table.add_column("Automation", max_width=12)
            table.add_column("Status")
            table.add_column("Triggered")
            table.add_column("Duration")
            table.add_column("Actions")

            for exec in executions:
                status_style = {
                    ExecutionStatus.SUCCESS: "[green]success[/green]",
                    ExecutionStatus.FAILED: "[red]failed[/red]",
                    ExecutionStatus.PARTIAL: "[yellow]partial[/yellow]",
                    ExecutionStatus.RUNNING: "[blue]running[/blue]",
                }.get(exec.status, str(exec.status))

                if exec.completed_at:
                    duration = exec.completed_at - exec.triggered_at
                    duration_str = f"{duration.total_seconds():.1f}s"
                else:
                    duration_str = "..."

                success = len([r for r in exec.action_results if r.status == "success"])
                failed = len([r for r in exec.action_results if r.status == "failed"])
                action_str = f"{success}✓ {failed}✗" if failed else f"{success}✓"

                table.add_row(
                    exec.id[:16],
                    exec.automation_id[:12],
                    status_style,
                    exec.triggered_at.strftime("%Y-%m-%d %H:%M"),
                    duration_str,
                    action_str,
                )

            console.print(table)
            return

        # Default: list automations
        filter_status = AutomationStatus(status) if status else None
        automations = await db.list_automations(status=filter_status)

        if not automations:
            console.print("[dim]No automations found[/dim]")
            return

        table = Table(title="Automations")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("Name", style="bold")
        table.add_column("Status", style="green")
        table.add_column("Trigger")
        table.add_column("Updated")

        for auto in automations:
            status_style = {
                AutomationStatus.ACTIVE: "[green]active[/green]",
                AutomationStatus.DRAFT: "[yellow]draft[/yellow]",
                AutomationStatus.PAUSED: "[blue]paused[/blue]",
                AutomationStatus.ERROR: "[red]error[/red]",
            }.get(auto.status, str(auto.status))

            table.add_row(
                auto.id[:12],
                auto.name,
                status_style,
                auto.trigger.type,
                auto.updated_at.strftime("%Y-%m-%d %H:%M"),
            )

        console.print(table)
    finally:
        await db.close()


@app.command("watch")
@async_command
async def watch_cmd(
    source: Annotated[
        str, typer.Argument(help="What to watch: 'email' or 'github'")
    ] = "email",
    interval: Annotated[
        int, typer.Option("--interval", "-i", help="Seconds between polls")
    ] = 60,
    once: Annotated[
        bool, typer.Option("--once", "-1", help="Run once and exit")
    ] = False,
):
    """Watch for trigger events and run automations.

    Polls email or GitHub for events matching active automation triggers.
    Press Ctrl+C to stop.

    Example:
        pai watch                     # Watch emails (default)
        pai watch email               # Watch emails explicitly
        pai watch github              # Watch GitHub PR reviews
        pai watch github -i 120       # GitHub with 2min interval
        pai watch --once              # Check once and exit
    """
    if source == "github":
        await _watch_github(interval, once)
        return

    from pai.watcher import EmailWatcher

    console.print(Panel.fit(
        f"[bold]Email Watcher[/bold]\n\n"
        f"Polling interval: {interval}s\n"
        f"Mode: {'single check' if once else 'continuous'}\n\n"
        f"[dim]Watching for emails that match active automation triggers.[/dim]",
        title="PAI Watcher",
    ))

    # Check for active automations
    db = get_db()
    await db.initialize()
    automations = await db.list_automations(status=AutomationStatus.ACTIVE)
    await db.close()

    email_automations = [
        a for a in automations
        if (isinstance(a.trigger, dict) and a.trigger.get("type") == "email")
        or (hasattr(a.trigger, "type") and a.trigger.type == "email")
    ]

    if not email_automations:
        console.print("\n[yellow]Warning:[/yellow] No active automations with email triggers.")
        console.print("[dim]Use 'pai list --status active' to see active automations.[/dim]")
        console.print("[dim]Use 'pai list --activate <id>' to activate an automation.[/dim]\n")
        if not once:
            console.print("[dim]Watcher will start anyway and check periodically...[/dim]\n")
    else:
        console.print(f"\n[green]Found {len(email_automations)} active email automation(s)[/green]")
        for auto in email_automations:
            trigger = auto.trigger if isinstance(auto.trigger, dict) else auto.trigger.model_dump()
            conditions = trigger.get("conditions", [])
            cond_str = ", ".join(f"{c.get('field')}~{c.get('value')}" for c in conditions[:2])
            console.print(f"  - [cyan]{auto.name}[/cyan] ({cond_str or 'all emails'})")
        console.print()

    watcher = EmailWatcher()

    if once:
        console.print("[dim]Running single check...[/dim]\n")
        await watcher.start(interval=interval, max_iterations=1)
        console.print("\n[green]Check complete.[/green]")
    else:
        console.print(f"[dim]Starting watcher (Ctrl+C to stop)...[/dim]\n")
        try:
            await watcher.start(interval=interval)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping watcher...[/yellow]")
            watcher.stop()
            console.print("[green]Watcher stopped.[/green]")


async def _watch_github(interval: int, once: bool) -> None:
    """Watch for GitHub PR reviews."""
    from pai.watcher import GitHubPRWatcher

    # Default to 2 minutes for GitHub to avoid rate limits
    if interval == 60:
        interval = 120

    console.print(Panel.fit(
        f"[bold]GitHub PR Review Watcher[/bold]\n\n"
        f"Polling interval: {interval}s\n"
        f"Mode: {'single check' if once else 'continuous'}\n\n"
        f"[dim]Watching for PR reviews on your pull requests.[/dim]",
        title="PAI GitHub Watcher",
    ))

    # Check for active automations
    db = get_db()
    await db.initialize()
    automations = await db.list_automations(status=AutomationStatus.ACTIVE)
    await db.close()

    github_automations = [
        a for a in automations
        if (isinstance(a.trigger, dict) and a.trigger.get("type") == "github_pr")
        or (hasattr(a.trigger, "type") and a.trigger.type == "github_pr")
    ]

    if not github_automations:
        console.print("\n[yellow]Warning:[/yellow] No active automations with github_pr triggers.")
        console.print("[dim]Use 'pai intent \"when someone reviews my PR...\" --plan --save'[/dim]")
        if not once:
            console.print("[dim]Watcher will start anyway and check periodically...[/dim]\n")
    else:
        console.print(f"\n[green]Found {len(github_automations)} active GitHub automation(s)[/green]")
        for auto in github_automations:
            console.print(f"  - [cyan]{auto.name}[/cyan]")
        console.print()

    watcher = GitHubPRWatcher()

    if once:
        console.print("[dim]Running single check...[/dim]\n")
        await watcher.start(interval=interval, max_iterations=1)
        console.print("\n[green]Check complete.[/green]")
    else:
        console.print(f"[dim]Starting watcher (Ctrl+C to stop)...[/dim]\n")
        try:
            await watcher.start(interval=interval)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping watcher...[/yellow]")
            watcher.stop()
            console.print("[green]Watcher stopped.[/green]")


# =============================================================================
# Query subgroup (emails, entities)
# =============================================================================

query_app = typer.Typer(help="Query data from connected services")
app.add_typer(query_app, name="query")


@query_app.command("emails")
@async_command
async def query_emails(
    query: Annotated[str, typer.Argument(help="Gmail search query")],
    max_results: Annotated[int, typer.Option("--max", "-m", help="Maximum results")] = 10,
    show_body: Annotated[bool, typer.Option("--body", "-b", help="Show email body")] = False,
):
    """Search emails with Gmail query syntax."""
    from pai.gmail import get_gmail_client

    client = get_gmail_client()
    with console.status(f"[bold blue]Searching: {query}[/bold blue]"):
        result = await client.search(query, max_results=max_results)

    if not result.emails:
        console.print("[dim]No emails found[/dim]")
        return

    console.print(f"\n[bold]Found {result.total_estimate} emails[/bold] (showing {len(result.emails)})\n")

    for email in result.emails:
        date_str = email.date.strftime("%Y-%m-%d %H:%M") if email.date else "Unknown"
        from_str = email.from_.name or email.from_.email
        label_str = " ".join(f"[{l}]" for l in email.labels if not l.startswith("CATEGORY_"))

        console.print(f"[cyan]{email.id[:12]}[/cyan] {date_str}")
        console.print(f"  [bold]{email.subject or '(no subject)'}[/bold]")
        console.print(f"  From: [green]{from_str}[/green] {label_str}")

        if show_body:
            body = email.body_text[:500] if email.body_text else email.snippet
            console.print(f"  [dim]{body}[/dim]")

        if email.attachments:
            console.print(f"  [yellow]Attachments:[/yellow] {', '.join(a.filename for a in email.attachments)}")
        console.print()


@query_app.command("entities")
@async_command
async def query_entities(
    entity_type: Annotated[
        Optional[str],
        typer.Option("--type", "-t", help="Filter by type (client, person, project)"),
    ] = None,
    discover: Annotated[
        bool, typer.Option("--discover", "-d", help="Discover entities from recent emails")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", "-l", help="Limit results")
    ] = 20,
):
    """List and discover entities from your data.

    Example:
        pai query entities                    # List all entities
        pai query entities --type client      # List only clients
        pai query entities --discover         # Discover from recent emails
    """
    from pai.models import EntityType

    db = get_db()
    await db.initialize()

    try:
        if discover:
            # Discover entities from emails
            from pai.gmail import EntityExtractor, get_gmail_client

            console.print("[bold]Discovering entities from recent emails...[/bold]\n")

            client = get_gmail_client()

            with console.status("[bold blue]Fetching recent emails...[/bold blue]"):
                result = await client.search("newer_than:30d", max_results=100)

            console.print(f"[dim]Analyzed {len(result.emails)} emails[/dim]\n")

            extractor = EntityExtractor()
            existing = await db.list_entities()
            new_entities = extractor.extract_from_emails(result.emails, existing)

            if not new_entities:
                console.print("[dim]No new entities discovered[/dim]")
            else:
                console.print(f"[green]Discovered {len(new_entities)} entities:[/green]\n")

                for entity in new_entities[:limit]:
                    console.print(f"  [{entity.type.value}] [bold]{entity.name}[/bold]")
                    console.print(f"    Aliases: {', '.join(entity.aliases)}")
                    console.print(f"    Emails: {entity.metadata.get('email_count', 0)}")
                    console.print()

                # Ask to save
                if typer.confirm("Save these entities?"):
                    for entity in new_entities:
                        await db.save_entity(entity)
                    console.print(f"[green]Saved {len(new_entities)} entities[/green]")
        else:
            # List existing entities
            filter_type = EntityType(entity_type) if entity_type else None
            entities = await db.list_entities(entity_type=filter_type)

            if not entities:
                console.print("[dim]No entities found. Use --discover to find entities from emails.[/dim]")
                return

            table = Table(title="Entities")
            table.add_column("Type", style="cyan")
            table.add_column("Name", style="bold")
            table.add_column("Aliases")
            table.add_column("Sources", style="dim")

            for entity in entities[:limit]:
                table.add_row(
                    entity.type.value,
                    entity.name,
                    ", ".join(entity.aliases[:3]),
                    str(len(entity.sources)),
                )

            console.print(table)
    finally:
        await db.close()


# =============================================================================
# MCP commands (Phase 6 - MCP Architecture)
# =============================================================================

mcp_app = typer.Typer(help="Manage MCP (Model Context Protocol) servers")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("list")
@async_command
async def mcp_list_cmd(
    server: Annotated[
        Optional[str],
        typer.Option("--server", "-s", help="Show tools for specific server only"),
    ] = None,
    tools: Annotated[
        bool, typer.Option("--tools", "-t", help="List tools for each server")
    ] = False,
):
    """List configured MCP servers and their tools.

    Example:
        pai mcp list                  # List all servers
        pai mcp list --tools          # List servers with their tools
        pai mcp list --server gmail   # Show tools for gmail server
    """
    from pai.mcp import get_mcp_manager

    manager = get_mcp_manager()
    config = manager.load_config()

    if not config.servers:
        console.print("[dim]No MCP servers configured[/dim]")
        console.print(f"[dim]Add servers to {manager._config_path}[/dim]")
        return

    if server:
        # Show specific server details
        server_config = manager.get_server_config(server)
        if not server_config:
            console.print(f"[red]Server not found:[/red] {server}")
            return

        console.print(f"\n[bold cyan]{server}[/bold cyan]")
        console.print(f"  Command: [green]{server_config.command} {' '.join(server_config.args)}[/green]")
        if server_config.env:
            console.print("  Environment:")
            for k, v in server_config.env.items():
                # Mask sensitive values
                display_v = "***" if "key" in k.lower() or "secret" in k.lower() else v
                console.print(f"    {k}: {display_v}")

        # List tools
        console.print("\n[bold]Tools:[/bold]")
        try:
            server_tools = await manager.list_tools(server)
            if server_tools:
                for tool in server_tools:
                    console.print(f"  [cyan]{tool.name}[/cyan]")
                    if tool.description:
                        console.print(f"    [dim]{tool.description[:100]}[/dim]")
            else:
                console.print("  [dim]No tools available[/dim]")
        except Exception as e:
            console.print(f"  [red]Error connecting: {e}[/red]")
    else:
        # List all servers
        table = Table(title="MCP Servers")
        table.add_column("Server", style="cyan")
        table.add_column("Command")
        table.add_column("Status")

        if tools:
            table.add_column("Tools")

        for name, srv_config in config.servers.items():
            cmd_display = f"{srv_config.command} {srv_config.args[0] if srv_config.args else ''}"

            if tools:
                try:
                    server_tools = await manager.list_tools(name)
                    status = "[green]connected[/green]"
                    tool_count = f"{len(server_tools)} tools"
                except Exception:
                    status = "[yellow]not running[/yellow]"
                    tool_count = "-"
                table.add_row(name, cmd_display, status, tool_count)
            else:
                table.add_row(name, cmd_display, "[dim]?[/dim]")

        console.print(table)

        if not tools:
            console.print("\n[dim]Use --tools to check server status and list tools[/dim]")


@mcp_app.command("auth")
@async_command
async def mcp_auth_cmd(
    server: Annotated[str, typer.Argument(help="Server name to authenticate")],
):
    """Trigger OAuth or authentication flow for an MCP server.

    Example:
        pai mcp auth gmail
    """
    from pai.mcp import get_mcp_manager

    manager = get_mcp_manager()
    server_config = manager.get_server_config(server)

    if not server_config:
        console.print(f"[red]Server not found:[/red] {server}")
        console.print(f"[dim]Configure in {manager._config_path}[/dim]")
        return

    console.print(f"[bold]Authenticating with {server}...[/bold]")

    # Try to call an auth-related tool or connect to trigger OAuth
    try:
        async with manager.connect(server) as session:
            # List tools to verify connection works
            tools = await session.list_tools()
            console.print(f"[green]Connected to {server}[/green]")
            console.print(f"[dim]{len(tools.tools)} tools available[/dim]")

            # Some MCP servers have an explicit auth tool
            tool_names = [t.name for t in tools.tools]
            if "authenticate" in tool_names:
                console.print("\n[bold]Running authentication...[/bold]")
                result = await session.call_tool("authenticate", {})
                console.print("[green]Authentication complete[/green]")
            elif "auth" in tool_names:
                console.print("\n[bold]Running authentication...[/bold]")
                result = await session.call_tool("auth", {})
                console.print("[green]Authentication complete[/green]")
            else:
                console.print("\n[yellow]Note:[/yellow] Server connected successfully.")
                console.print("[dim]This server may handle auth automatically or via environment variables.[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("1. Check server command and args are correct")
        console.print("2. Verify environment variables are set")
        console.print("3. Try running the server command manually")


@mcp_app.command("add")
def mcp_add_cmd(
    name: Annotated[str, typer.Argument(help="Server name (e.g., 'gmail')")],
    command: Annotated[str, typer.Argument(help="Command to run (e.g., 'uvx')")],
    args: Annotated[
        Optional[list[str]],
        typer.Argument(help="Command arguments"),
    ] = None,
):
    """Add a new MCP server configuration.

    Example:
        pai mcp add gmail uvx mcp-gmail
        pai mcp add sheets npx -y @anthropic/mcp-google-sheets
    """
    from pai.mcp import MCPServerConfig, get_mcp_manager

    manager = get_mcp_manager()
    config = manager.load_config()

    if name in config.servers:
        if not typer.confirm(f"Server '{name}' already exists. Overwrite?"):
            console.print("[dim]Cancelled[/dim]")
            return

    config.servers[name] = MCPServerConfig(
        command=command,
        args=args or [],
        env={},
    )

    manager.save_config(config)
    console.print(f"[green]Added MCP server:[/green] {name}")
    console.print(f"[dim]Edit {manager._config_path} to add environment variables[/dim]")


if __name__ == "__main__":
    app()
