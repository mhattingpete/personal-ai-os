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
):
    """Parse a natural language intent into an automation spec.

    Example:
        pai intent "When a client emails me with an invoice, save it to their folder"
    """
    console.print(Panel.fit(
        f"[bold]Input:[/bold] {text}",
        title="Intent Parsing",
    ))

    console.print("\n[dim]Intent engine not yet implemented (Phase 2)[/dim]")
    console.print("[dim]This command will parse natural language and create automation specs[/dim]")


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
