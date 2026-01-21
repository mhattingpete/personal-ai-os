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
):
    """Connect to an external service.

    Example:
        pai connect google
    """
    console.print(f"[dim]Connector for '{service}' not yet implemented (Phase 3)[/dim]")


if __name__ == "__main__":
    app()
