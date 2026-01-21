"""CLI entry point for PAI.

Uses Typer for command parsing and Rich for output.
Run with: uv run pai --help
"""

import asyncio
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pai.config import get_config_dir, get_data_dir, get_settings
from pai.db import init_db
from pai.models import AutomationStatus

app = typer.Typer(
    name="pai",
    help="Personal AI OS - automate your digital life with natural language",
    no_args_is_help=True,
)
console = Console()


def run_async(coro):
    """Run an async function in the event loop."""
    return asyncio.run(coro)


@app.callback()
def callback(
    debug: Annotated[
        bool, typer.Option("--debug", "-d", help="Enable debug output")
    ] = False,
):
    """PAI - Personal AI Operating System."""
    if debug:
        console.print("[dim]Debug mode enabled[/dim]")


@app.command()
def init():
    """Initialize PAI (create config and database)."""

    async def _init():
        # Show paths
        config_dir = get_config_dir()
        data_dir = get_data_dir()

        console.print(Panel.fit(
            f"[bold]Config:[/bold] {config_dir}\n"
            f"[bold]Data:[/bold] {data_dir}",
            title="PAI Directories",
        ))

        # Initialize database
        db = await init_db()
        console.print("[green]✓[/green] Database initialized")

        # Check for API key
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

    run_async(_init())


@app.command()
def config(
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


@app.command()
def status():
    """Show PAI status."""

    async def _status():
        from pai.db import get_db

        db = get_db()

        try:
            # Check database
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

    run_async(_status())


# =============================================================================
# Automation commands (scaffolding for Phase 4)
# =============================================================================


@app.command("list")
def list_automations(
    status: Annotated[
        Optional[str],
        typer.Option("--status", "-s", help="Filter by status (draft, active, paused, error)"),
    ] = None,
):
    """List all automations."""

    async def _list():
        from pai.db import get_db

        db = get_db()
        await db.initialize()

        try:
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

    run_async(_list())


# =============================================================================
# Intent command (scaffolding for Phase 2)
# =============================================================================


@app.command()
def intent(
    text: Annotated[str, typer.Argument(help="Natural language intent to parse")],
    plan: Annotated[
        bool, typer.Option("--plan", "-p", help="Generate full automation spec")
    ] = False,
    save: Annotated[
        bool, typer.Option("--save", "-s", help="Save the automation to database")
    ] = False,
    local: Annotated[
        bool, typer.Option("--local", "-l", help="Use local LLM (llama.cpp)")
    ] = False,
):
    """Parse a natural language intent into an automation spec.

    Example:
        pai intent "When a client emails me with an invoice, save it to their folder"
        pai intent "Label emails from acme.com as Client" --plan
        pai intent "Save PDF attachments to Dropbox" --local
    """
    from rich.syntax import Syntax

    from pai.intent import IntentEngine
    from pai.llm import get_provider

    async def _intent():
        console.print(Panel.fit(
            f"[bold]Input:[/bold] {text}",
            title="Intent Parsing",
        ))

        # Choose provider
        provider = get_provider("local" if local else "claude")
        if local:
            console.print("[dim]Using local LLM (llama.cpp)[/dim]\n")

        engine = IntentEngine(provider)

        # Stage 1: Parse
        with console.status("[bold blue]Parsing intent...[/bold blue]"):
            result = await engine.parse(text)

        intent_graph = result.intent

        # Display parsed intent
        console.print("\n[bold cyan]Parsed Intent[/bold cyan]")
        console.print(f"  Type: [green]{intent_graph.type}[/green]")
        console.print(f"  Confidence: [yellow]{intent_graph.confidence:.0%}[/yellow]")

        if intent_graph.trigger:
            console.print(f"  Trigger: [blue]{intent_graph.trigger.type}[/blue]")
            if intent_graph.trigger.conditions:
                for cond in intent_graph.trigger.conditions:
                    console.print(f"    - {cond}")

        if intent_graph.actions:
            console.print("  Actions:")
            for action in intent_graph.actions:
                console.print(
                    f"    - [magenta]{action.type}[/magenta] "
                    f"(confidence: {action.confidence:.0%})"
                )
                if action.params:
                    for k, v in action.params.items():
                        console.print(f"      {k}: {v}")

        # Display ambiguities
        if intent_graph.ambiguities:
            console.print("\n[bold yellow]Ambiguities Detected[/bold yellow]")
            for amb in intent_graph.ambiguities:
                console.print(f"  [yellow]?[/yellow] {amb.description}")
                for q in amb.suggested_questions:
                    console.print(f"    - {q}")

        # Stage 2: Clarify (if needed)
        if result.needs_clarification:
            console.print("\n[dim]Run with --plan after clarifying ambiguities[/dim]")

            # Generate clarification questions
            with console.status("[bold blue]Generating questions...[/bold blue]"):
                clarify_result = await engine.clarify(intent_graph)

            if clarify_result.questions:
                console.print("\n[bold cyan]Clarification Questions[/bold cyan]")
                for i, q in enumerate(clarify_result.questions, 1):
                    console.print(f"\n  {i}. {q.question}")
                    if q.options:
                        for opt in q.options:
                            default = " [dim](default)[/dim]" if opt == q.default else ""
                            console.print(f"     - {opt}{default}")
            return

        # Stage 3: Plan (if requested or ready)
        if plan or result.ready_for_planning:
            console.print("\n[bold cyan]Generating Automation Spec...[/bold cyan]")

            with console.status("[bold blue]Planning automation...[/bold blue]"):
                plan_result = await engine.plan(intent_graph)

            automation = plan_result.automation

            console.print(f"\n[bold green]Automation: {automation.name}[/bold green]")
            console.print(f"  {plan_result.summary}")

            # Show YAML-like representation
            auto_dict = {
                "name": automation.name,
                "description": automation.description,
                "trigger": {
                    "type": automation.trigger.type,
                },
                "actions": [
                    {"type": a.type} for a in automation.actions
                ],
            }

            import yaml
            yaml_str = yaml.dump(auto_dict, default_flow_style=False, sort_keys=False)
            console.print("\n[bold]Automation Spec:[/bold]")
            console.print(Syntax(yaml_str, "yaml", theme="monokai"))

            # Show warnings
            if plan_result.warnings:
                console.print("\n[bold yellow]Warnings:[/bold yellow]")
                for w in plan_result.warnings:
                    console.print(f"  [yellow]![/yellow] {w}")

            # Save if requested
            if save:
                from pai.db import get_db
                db = get_db()
                await db.initialize()
                await db.save_automation(automation)
                await db.close()
                console.print(f"\n[green]Saved automation:[/green] {automation.id}")

    run_async(_intent())


# =============================================================================
# Connect command (scaffolding for Phase 3)
# =============================================================================


@app.command()
def connect(
    service: Annotated[str, typer.Argument(help="Service to connect (google, notion, etc.)")],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Force re-authentication")
    ] = False,
):
    """Connect to an external service.

    Example:
        pai connect google
    """
    if service.lower() not in ("google", "gmail"):
        console.print(f"[yellow]Connector for '{service}' not yet implemented[/yellow]")
        console.print("[dim]Supported: google, gmail[/dim]")
        return

    async def _connect():
        from pai.db import get_db
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

            # Save connector to database
            db = get_db()
            await db.initialize()
            await db.save_connector(connector)
            await db.close()

            console.print(f"\n[green]Connected to Gmail as:[/green] {connector.account_id}")

            # Show labels
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

    run_async(_connect())


# =============================================================================
# Email commands (Phase 3)
# =============================================================================


@app.command()
def emails(
    query: Annotated[str, typer.Argument(help="Gmail search query")],
    max_results: Annotated[
        int, typer.Option("--max", "-m", help="Maximum results to show")
    ] = 10,
    show_body: Annotated[
        bool, typer.Option("--body", "-b", help="Show email body")
    ] = False,
):
    """Search emails with Gmail query syntax.

    Example:
        pai emails "from:client@example.com"
        pai emails "has:attachment invoice" --max 5
        pai emails "newer_than:7d subject:report"
    """

    async def _search():
        from pai.gmail import get_gmail_client

        client = get_gmail_client()

        with console.status(f"[bold blue]Searching: {query}[/bold blue]"):
            result = await client.search(query, max_results=max_results)

        if not result.emails:
            console.print("[dim]No emails found[/dim]")
            return

        console.print(f"\n[bold]Found {result.total_estimate} emails[/bold] (showing {len(result.emails)})\n")

        for email in result.emails:
            # Format date
            date_str = email.date.strftime("%Y-%m-%d %H:%M") if email.date else "Unknown"

            # Format from
            from_str = email.from_.name or email.from_.email

            # Labels
            label_str = " ".join(f"[{l}]" for l in email.labels if not l.startswith("CATEGORY_"))

            console.print(f"[cyan]{email.id[:12]}[/cyan] {date_str}")
            console.print(f"  [bold]{email.subject or '(no subject)'}[/bold]")
            console.print(f"  From: [green]{from_str}[/green] {label_str}")

            if show_body:
                body = email.body_text[:500] if email.body_text else email.snippet
                console.print(f"  [dim]{body}[/dim]")

            if email.attachments:
                att_names = ", ".join(a.filename for a in email.attachments)
                console.print(f"  [yellow]Attachments:[/yellow] {att_names}")

            console.print()

    run_async(_search())


# =============================================================================
# Execution commands (Phase 4)
# =============================================================================


@app.command("run")
def run_automation_cmd(
    automation_id: Annotated[str, typer.Argument(help="Automation ID to run")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Simulate without executing")
    ] = False,
):
    """Run an automation manually.

    Example:
        pai run abc123 --dry-run    # Preview what would happen
        pai run abc123              # Actually run the automation
    """
    from rich.json import JSON

    async def _run():
        from pai.executor import run_automation

        mode = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]EXECUTING[/green]"
        console.print(f"\n{mode} automation: [cyan]{automation_id}[/cyan]\n")

        try:
            execution = await run_automation(automation_id, dry_run=dry_run)

            # Show results
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
                    action_status = (
                        "[green]✓[/green]" if result.status == "success"
                        else "[red]✗[/red]" if result.status == "failed"
                        else "[yellow]○[/yellow]"
                    )
                    console.print(f"  {action_status} {result.action_id}")

                    if result.output:
                        if dry_run and result.output.get("description"):
                            console.print(f"      [dim]{result.output['description']}[/dim]")
                        elif not dry_run:
                            console.print(f"      [dim]{result.output}[/dim]")

                    if result.error:
                        console.print(f"      [red]Error: {result.error}[/red]")

            if execution.error:
                console.print(f"\n[red]Error:[/red] {execution.error.message}")

        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")

    run_async(_run())


@app.command("activate")
def activate_automation_cmd(
    automation_id: Annotated[str, typer.Argument(help="Automation ID to activate")],
):
    """Activate an automation (set status to active).

    Example:
        pai activate abc123
    """

    async def _activate():
        from pai.executor import activate_automation

        try:
            automation = await activate_automation(automation_id)
            console.print(f"[green]Activated:[/green] {automation.name}")
            console.print(f"[dim]ID: {automation.id}[/dim]")
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")

    run_async(_activate())


@app.command("pause")
def pause_automation_cmd(
    automation_id: Annotated[str, typer.Argument(help="Automation ID to pause")],
):
    """Pause an automation (set status to paused).

    Example:
        pai pause abc123
    """

    async def _pause():
        from pai.executor import pause_automation

        try:
            automation = await pause_automation(automation_id)
            console.print(f"[yellow]Paused:[/yellow] {automation.name}")
            console.print(f"[dim]ID: {automation.id}[/dim]")
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")

    run_async(_pause())


@app.command("delete")
def delete_automation_cmd(
    automation_id: Annotated[str, typer.Argument(help="Automation ID to delete")],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Skip confirmation")
    ] = False,
):
    """Delete an automation.

    Example:
        pai delete abc123
        pai delete abc123 --force
    """

    async def _delete():
        from pai.db import get_db

        db = get_db()
        await db.initialize()

        # Get automation first to show name
        automation = await db.get_automation(automation_id)
        if not automation:
            console.print(f"[red]Error:[/red] Automation not found: {automation_id}")
            await db.close()
            return

        # Confirm unless --force
        if not force:
            if not typer.confirm(f"Delete automation '{automation.name}'?"):
                console.print("[dim]Cancelled[/dim]")
                await db.close()
                return

        # Delete
        deleted = await db.delete_automation(automation_id)
        await db.close()

        if deleted:
            console.print(f"[green]Deleted:[/green] {automation.name}")
        else:
            console.print(f"[red]Error:[/red] Failed to delete")

    run_async(_delete())


@app.command("history")
def history_cmd(
    automation_id: Annotated[
        Optional[str],
        typer.Option("--automation", "-a", help="Filter by automation ID"),
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option("--status", "-s", help="Filter by status (success, failed, running)"),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-l", help="Maximum results")
    ] = 20,
):
    """Show execution history.

    Example:
        pai history                       # Show recent executions
        pai history --automation abc123   # Filter by automation
        pai history --status failed       # Show only failures
    """
    from pai.models import ExecutionStatus

    async def _history():
        from pai.db import get_db

        db = get_db()
        await db.initialize()

        try:
            filter_status = ExecutionStatus(status) if status else None
            executions = await db.list_executions(
                automation_id=automation_id,
                status=filter_status,
                limit=limit,
            )

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
                # Format status
                status_style = {
                    ExecutionStatus.SUCCESS: "[green]success[/green]",
                    ExecutionStatus.FAILED: "[red]failed[/red]",
                    ExecutionStatus.PARTIAL: "[yellow]partial[/yellow]",
                    ExecutionStatus.RUNNING: "[blue]running[/blue]",
                }.get(exec.status, str(exec.status))

                # Calculate duration
                if exec.completed_at:
                    duration = exec.completed_at - exec.triggered_at
                    duration_str = f"{duration.total_seconds():.1f}s"
                else:
                    duration_str = "..."

                # Count action results
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
        finally:
            await db.close()

    run_async(_history())


# =============================================================================
# Entity commands (Phase 3)
# =============================================================================


@app.command("entities")
def entities_cmd(
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
        pai entities                    # List all entities
        pai entities --type client      # List only clients
        pai entities --discover         # Discover from recent emails
    """
    from pai.models import EntityType

    async def _entities():
        from pai.db import get_db

        db = get_db()
        await db.initialize()

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
                await db.close()
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

        await db.close()

    run_async(_entities())


if __name__ == "__main__":
    app()
