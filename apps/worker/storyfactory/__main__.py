"""StoryFactory Worker CLI entry point."""

import sys
import click
from rich.console import Console
from dotenv import load_dotenv

load_dotenv()

console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """StoryFactory Worker - Content generation engine."""
    if ctx.invoked_subcommand is None:
        console.print("[bold blue]StoryFactory Worker[/bold blue] v1.0.0")
        console.print("Use --help to see available commands.")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Run without API calls or uploads")
@click.option("--channel", "channel_ref", default=None, help="Channel id/slug (default channel if omitted)")
@click.option("--all-active", "all_active", is_flag=True, help="Run for every active channel")
def daily(dry_run: bool, channel_ref: str | None, all_active: bool):
    """Run the full daily content generation pipeline."""
    from storyfactory.pipeline.daily import run_daily_pipeline, run_daily_pipeline_all_active

    console.print("[bold green]Starting daily pipeline...[/bold green]")
    if all_active:
        run_daily_pipeline_all_active(dry_run=dry_run)
    else:
        run_daily_pipeline(channel_ref=channel_ref, dry_run=dry_run)


@cli.command()
@click.option("--batch-id", help="Render a specific batch")
@click.option("--channel", "channel_ref", default=None, help="Render only this channel's batches")
@click.option("--dry-run", is_flag=True, help="Skip actual rendering")
def render(batch_id: str | None, channel_ref: str | None, dry_run: bool):
    """Process the render queue."""
    from storyfactory.pipeline.render import run_render_pipeline

    console.print("[bold green]Starting render pipeline...[/bold green]")
    run_render_pipeline(batch_id=batch_id, channel_ref=channel_ref, dry_run=dry_run)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Simulate uploads without calling YouTube API")
@click.option("--channel", "channel_ref", default=None, help="Channel id/slug (default channel if omitted)")
@click.option("--all-active", "all_active", is_flag=True, help="Upload for every active channel")
def upload(dry_run: bool, channel_ref: str | None, all_active: bool):
    """Upload rendered videos to YouTube."""
    from storyfactory.pipeline.upload import run_upload_pipeline, run_upload_pipeline_all_active

    console.print("[bold green]Starting upload pipeline...[/bold green]")
    if all_active:
        run_upload_pipeline_all_active(dry_run=dry_run)
    else:
        run_upload_pipeline(channel_ref=channel_ref, dry_run=dry_run)


@cli.command("retitle-legacy-long-form")
@click.option("--dry-run", is_flag=True, help="Preview title changes without updating YouTube or the database")
@click.option("--limit", type=int, default=None, help="Maximum number of matching uploads to retitle")
def retitle_legacy_long_form(dry_run: bool, limit: int | None):
    """Retitle uploaded long-form videos that still use the legacy generic title."""
    from storyfactory.services.youtube_uploader import retitle_legacy_long_form_uploads

    mode = "Previewing" if dry_run else "Updating"
    console.print(f"[bold green]{mode} legacy long-form titles...[/bold green]")

    results = retitle_legacy_long_form_uploads(dry_run=dry_run, limit=limit)
    if not results:
        console.print("[yellow]No legacy long-form uploads found.[/yellow]")
        return

    for result in results:
        status = result["status"]
        color = {
            "dry_run": "blue",
            "updated": "green",
            "skipped": "yellow",
            "failed": "red",
        }.get(status, "white")
        console.print(
            f"[{color}]{status.upper()}[/] {result['youtube_video_id']}: "
            f"{result['old_title']} -> {result['new_title']}"
        )
        if result.get("error"):
            console.print(f"  [red]{result['error']}[/red]")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Run daily pipeline and upload with dry-run configurations")
@click.option("--channel", "channel_ref", default=None, help="Channel id/slug (default channel if omitted)")
@click.option("--all-active", "all_active", is_flag=True, help="Run for every active channel")
def full(dry_run: bool, channel_ref: str | None, all_active: bool):
    """Run the daily pipeline and upload the results to YouTube in one step."""
    from storyfactory.pipeline.daily import run_daily_pipeline, run_daily_pipeline_all_active
    from storyfactory.pipeline.upload import run_upload_pipeline, run_upload_pipeline_all_active

    console.print("[bold green]Starting full end-to-end pipeline...[/bold green]")
    if all_active:
        run_daily_pipeline_all_active(dry_run=dry_run)
        run_upload_pipeline_all_active(dry_run=dry_run)
    else:
        run_daily_pipeline(channel_ref=channel_ref, dry_run=dry_run)
        run_upload_pipeline(channel_ref=channel_ref, dry_run=dry_run)


@cli.command()
@click.option("--channel", "channel_ref", default=None, help="Channel id/slug (all active channels if omitted)")
def analytics(channel_ref: str | None):
    """Fetch YouTube analytics snapshots (per channel)."""
    from storyfactory.pipeline.analytics import run_analytics_pipeline

    console.print("[bold green]Fetching analytics...[/bold green]")
    run_analytics_pipeline(channel_ref=channel_ref)


@cli.command()
def scheduler():
    """Run the daily scheduler (cron-like loop)."""
    from storyfactory.pipeline.scheduler import run_scheduler

    console.print("[bold green]Starting scheduler...[/bold green]")
    run_scheduler()


@cli.command()
@click.option("--sample", is_flag=True, help="Generate sample seed data")
def seed(sample: bool):
    """Seed the database with initial data."""
    from storyfactory.db.seed import run_seed

    console.print("[bold green]Seeding database...[/bold green]")
    run_seed(sample=sample)


# ============================================
# Channel management
# ============================================

@cli.group()
def channel():
    """Create and configure content channels."""


@channel.command("generate-key")
def channel_generate_key():
    """Print a fresh master encryption key for STORYFACTORY_SECRET_KEY."""
    from storyfactory.crypto import generate_key

    console.print(generate_key())


@channel.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def channel_list(as_json: bool):
    """List channels."""
    import json as _json

    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.channel_service import list_channels

    init_db()
    session = get_session()
    try:
        channels = list_channels(session)
        if as_json:
            console.print(
                _json.dumps(
                    [
                        {
                            "id": c.id,
                            "slug": c.slug,
                            "name": c.name,
                            "status": c.status,
                            "niche": c.niche,
                        }
                        for c in channels
                    ]
                )
            )
            return
        if not channels:
            console.print("[yellow]No channels yet. Run: python -m storyfactory channel import-env[/yellow]")
            return
        for c in channels:
            console.print(f"[green]{c.slug}[/green] — {c.name} ({c.status})")
    finally:
        session.close()


@channel.command("create")
@click.option("--name", required=True, help="Display name")
@click.option("--slug", default=None, help="Optional slug (derived from name if omitted)")
@click.option("--niche", default=None, help="What this channel publishes")
@click.option("--description", default=None)
@click.option("--import-env", "do_import_env", is_flag=True, help="Backfill integrations from current env")
def channel_create(name, slug, niche, description, do_import_env):
    """Create a new channel."""
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.channel_service import create_channel, import_env_into_channel

    init_db()
    session = get_session()
    try:
        ch = create_channel(
            session, name=name, slug=slug, niche=niche, description=description, commit=False
        )
        if do_import_env:
            import_env_into_channel(session, ch, commit=False)
        session.commit()
        console.print(f"[bold green]✓ Created channel '{ch.slug}'[/bold green]")
    finally:
        session.close()


@channel.command("import-env")
@click.option("--channel", "channel_ref", default=None, help="Channel id/slug (default channel if omitted)")
def channel_import_env(channel_ref):
    """Backfill a channel's integrations from the legacy global environment."""
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.channel_service import (
        ensure_default_channel,
        get_channel,
        import_env_into_channel,
    )

    init_db()
    session = get_session()
    try:
        if channel_ref:
            ch = get_channel(session, channel_ref)
            if ch is None:
                console.print(f"[red]Channel not found: {channel_ref}[/red]")
                return
            import_env_into_channel(session, ch, commit=True)
        else:
            ch = ensure_default_channel(session, commit=True)
        console.print(f"[bold green]✓ Imported env into channel '{ch.slug}'[/bold green]")
    finally:
        session.close()


def _parse_kv(pairs: tuple[str, ...]) -> dict:
    result = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.BadParameter(f"Expected field=value, got: {pair}")
        key, value = pair.split("=", 1)
        result[key.strip()] = value
    return result


@channel.command("set-secret")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
@click.option("--provider", required=True, help="Provider key (e.g. text.openai, youtube)")
@click.option("--secret", "secrets", multiple=True, help="field=value (repeatable)")
@click.option("--config", "configs", multiple=True, help="field=value (repeatable)")
@click.option("--enable/--disable", "enable", default=None, help="Toggle the integration")
def channel_set_secret(channel_ref, provider, secrets, configs, enable):
    """Set (encrypted) secrets and/or config for a channel integration."""
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.channel_service import get_channel, set_integration

    init_db()
    session = get_session()
    try:
        ch = get_channel(session, channel_ref)
        if ch is None:
            console.print(f"[red]Channel not found: {channel_ref}[/red]")
            return
        set_integration(
            session,
            ch,
            provider,
            enabled=enable,
            secrets=_parse_kv(secrets) or None,
            config=_parse_kv(configs) or None,
            commit=True,
        )
        # Never echo secret values back.
        console.print(f"[bold green]✓ Updated {provider} for '{ch.slug}'[/bold green]")
    finally:
        session.close()


@channel.command("set-youtube-token")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
@click.option("--token", required=True, help="OAuth refresh token")
def channel_set_youtube_token(channel_ref, token):
    """Store an encrypted per-channel YouTube refresh token (used by OAuth callback)."""
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.channel_service import get_channel, set_integration

    init_db()
    session = get_session()
    try:
        ch = get_channel(session, channel_ref)
        if ch is None:
            console.print(f"[red]Channel not found: {channel_ref}[/red]")
            return
        set_integration(
            session, ch, "youtube", enabled=True, secrets={"refresh_token": token}, commit=True
        )
        console.print(f"[bold green]✓ Stored YouTube token for '{ch.slug}'[/bold green]")
    finally:
        session.close()


def _with_channel(channel_ref):
    """Resolve a channel by ref, printing an error and returning (session, None) if missing."""
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.channel_service import get_channel

    init_db()
    session = get_session()
    ch = get_channel(session, channel_ref)
    if ch is None:
        console.print(f"[red]Channel not found: {channel_ref}[/red]")
    return session, ch


@channel.command("update")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
@click.option("--name", default=None)
@click.option("--description", default=None)
@click.option("--niche", default=None)
@click.option("--content-style", "content_style", default=None, help="Free-text content style descriptor")
@click.option("--status", default=None, type=click.Choice(["active", "paused"]))
def channel_update(channel_ref, name, description, niche, content_style, status):
    """Update a channel's editable (non-secret) fields."""
    from storyfactory.services.channel_service import update_channel

    session, ch = _with_channel(channel_ref)
    try:
        if ch is None:
            return
        update_channel(
            session,
            ch,
            name=name,
            description=description,
            niche=niche,
            content_style=content_style,
            status=status,
            commit=True,
        )
        console.print(f"[bold green]✓ Updated channel '{ch.slug}'[/bold green]")
    finally:
        session.close()


@channel.command("set-status")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
@click.option("--status", required=True, type=click.Choice(["active", "paused"]))
def channel_set_status(channel_ref, status):
    """Activate or pause a channel."""
    from storyfactory.services.channel_service import update_channel

    session, ch = _with_channel(channel_ref)
    try:
        if ch is None:
            return
        update_channel(session, ch, status=status, commit=True)
        console.print(f"[bold green]✓ Channel '{ch.slug}' is now {status}[/bold green]")
    finally:
        session.close()


@channel.command("duplicate")
@click.option("--channel", "channel_ref", required=True, help="Source channel id/slug")
@click.option("--name", required=True, help="New channel display name")
@click.option("--slug", default=None, help="New channel slug")
def channel_duplicate(channel_ref, name, slug):
    """Clone a channel's config + integrations into a new (paused) channel."""
    from storyfactory.services.channel_service import duplicate_channel

    session, ch = _with_channel(channel_ref)
    try:
        if ch is None:
            return
        clone = duplicate_channel(session, ch, new_name=name, new_slug=slug, commit=True)
        console.print(f"[bold green]✓ Duplicated '{ch.slug}' -> '{clone.slug}' (paused)[/bold green]")
    finally:
        session.close()


@channel.command("delete")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
def channel_delete(channel_ref):
    """Delete a channel (only if it has no batch history)."""
    from storyfactory.services.channel_service import delete_channel

    session, ch = _with_channel(channel_ref)
    try:
        if ch is None:
            return
        try:
            delete_channel(session, ch, commit=True)
            console.print(f"[bold green]✓ Deleted channel '{channel_ref}'[/bold green]")
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
    finally:
        session.close()


@channel.command("set-prompt")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
@click.option("--name", required=True, help="Prompt template name (e.g. short_story_generation)")
@click.option("--template", "template_text", default=None, help="Override template text")
@click.option("--template-file", default=None, type=click.Path(exists=True), help="Read template from a file")
def channel_set_prompt(channel_ref, name, template_text, template_file):
    """Create or update a per-channel prompt override (falls back to global)."""
    from storyfactory.services.channel_service import set_prompt_override

    if not template_text and not template_file:
        console.print("[red]Provide --template or --template-file[/red]")
        return
    if template_file:
        with open(template_file, "r", encoding="utf-8") as f:
            template_text = f.read()

    session, ch = _with_channel(channel_ref)
    try:
        if ch is None:
            return
        set_prompt_override(session, ch, name, template_text, commit=True)
        console.print(f"[bold green]✓ Set prompt override '{name}' for '{ch.slug}'[/bold green]")
    finally:
        session.close()


@channel.command("status")
@click.option("--channel", "channel_ref", required=True, help="Channel id/slug")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def channel_status(channel_ref, as_json):
    """Show per-integration status for a channel (no secret values)."""
    import json as _json

    from storyfactory.services.channel_service import integration_status

    session, ch = _with_channel(channel_ref)
    try:
        if ch is None:
            return
        statuses = integration_status(session, ch)
        if as_json:
            console.print(_json.dumps(statuses))
            return
        for st in statuses:
            mark = "✓" if st["configured"] else "·"
            state = "enabled" if st["enabled"] else "disabled"
            console.print(f"  [{mark}] {st['label']:18} {state}")
    finally:
        session.close()


# ============================================
# App-level (shared) integrations
# ============================================

@cli.group()
def settings():
    """Configure shared, app-level integrations used by every channel."""


@settings.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
def settings_status(as_json):
    """Show shared app-level integration status (no secret values)."""
    import json as _json

    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.app_integration_service import app_integration_status

    init_db()
    session = get_session()
    try:
        statuses = app_integration_status(session)
        if as_json:
            console.print(_json.dumps(statuses))
            return
        for st in statuses:
            mark = "✓" if st["configured"] else "·"
            state = "enabled" if st["enabled"] else "disabled"
            console.print(f"  [{mark}] {st['label']:18} {state}")
    finally:
        session.close()


@settings.command("set-secret")
@click.option("--provider", required=True, help="App-scoped provider key (e.g. text.openai)")
@click.option("--config", "configs", multiple=True, help="field=value (repeatable, non-secret)")
@click.option("--enable/--disable", "enable", default=None, help="Toggle the integration")
@click.option(
    "--secrets-stdin",
    is_flag=True,
    help="Read a JSON object of secret fields from stdin (avoids exposing secrets in argv)",
)
def settings_set_secret(provider, configs, enable, secrets_stdin):
    """Set (encrypted) secrets and/or config for a shared app-level integration.

    Secrets are read from stdin as a JSON object when --secrets-stdin is passed,
    so they never appear in the process argument list.
    """
    import json as _json
    import sys as _sys

    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.app_integration_service import set_app_integration

    secrets = None
    if secrets_stdin:
        raw = _sys.stdin.read()
        if raw.strip():
            try:
                parsed = _json.loads(raw)
            except _json.JSONDecodeError as exc:
                console.print(f"[red]Invalid JSON on stdin: {exc}[/red]")
                return
            if not isinstance(parsed, dict):
                console.print("[red]stdin must be a JSON object of secret fields[/red]")
                return
            secrets = {k: str(v) for k, v in parsed.items() if v not in (None, "")}

    init_db()
    session = get_session()
    try:
        try:
            set_app_integration(
                session,
                provider,
                enabled=enable,
                secrets=secrets or None,
                config=_parse_kv(configs) or None,
                commit=True,
            )
        except (ValueError, KeyError) as exc:
            console.print(f"[red]{exc}[/red]")
            return
        # Never echo secret values back.
        console.print(f"[bold green]✓ Updated app integration {provider}[/bold green]")
    finally:
        session.close()


@settings.command("import-env")
def settings_import_env():
    """Seed shared app-level integrations from the current .env (fills gaps only)."""
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.app_integration_service import import_env_into_app

    init_db()
    session = get_session()
    try:
        import_env_into_app(session, commit=True)
        console.print("[bold green]✓ Imported shared integrations from .env[/bold green]")
    finally:
        session.close()


if __name__ == "__main__":
    cli()
