"""Scheduler - runs the daily pipeline on a schedule."""

import schedule
import time

from rich.console import Console
from storyfactory.logger import setup_logging, get_logger

console = Console()
log = get_logger("scheduler")


def run_scheduler():
    """Run the daily scheduler loop."""
    setup_logging()
    console.print("[bold blue]StoryFactory Scheduler Started[/bold blue]")
    console.print("Daily pipeline will run at 06:00 UTC")
    console.print("Analytics will run at 22:00 UTC")
    console.print("Press Ctrl+C to stop\n")

    # Schedule daily pipeline at 6 AM UTC
    schedule.every().day.at("06:00").do(_run_daily_job)

    # Schedule analytics at 10 PM UTC
    schedule.every().day.at("22:00").do(_run_analytics_job)

    # Schedule upload check every 2 hours
    schedule.every(2).hours.do(_run_upload_job)

    while True:
        schedule.run_pending()
        time.sleep(60)


def _run_daily_job():
    """Execute the daily pipeline for every active channel."""
    from storyfactory.pipeline.daily import run_daily_pipeline_all_active
    log.info("scheduler_running_daily")
    console.print("[blue]Scheduler: Running daily pipeline (all active channels)...[/blue]")
    try:
        run_daily_pipeline_all_active()
        log.info("scheduler_daily_complete")
    except Exception as e:
        log.error("scheduler_daily_failed", error=str(e))
        console.print(f"[red]Daily pipeline failed: {e}[/red]")


def _run_upload_job():
    """Execute the upload pipeline for every active channel."""
    from storyfactory.pipeline.upload import run_upload_pipeline_all_active
    log.info("scheduler_running_upload")
    try:
        run_upload_pipeline_all_active()
    except Exception as e:
        log.error("scheduler_upload_failed", error=str(e))


def _run_analytics_job():
    """Execute the analytics pipeline."""
    from storyfactory.pipeline.analytics import run_analytics_pipeline
    log.info("scheduler_running_analytics")
    try:
        run_analytics_pipeline()
    except Exception as e:
        log.error("scheduler_analytics_failed", error=str(e))
