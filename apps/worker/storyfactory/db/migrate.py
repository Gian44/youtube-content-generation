"""Database migration management."""

import sys
from rich.console import Console
from storyfactory.db.migrations import run_migrations as _run_versioned_migrations

console = Console()


def run_migrations():
    """Create tables and apply versioned migrations (incl. channel backfill)."""
    console.print("[bold blue]Running database migrations...[/bold blue]")
    try:
        _run_versioned_migrations()
        console.print("[bold green]✓ Migrations applied successfully.[/bold green]")
    except Exception as e:
        console.print(f"[bold red]✗ Migration failed: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_migrations()
