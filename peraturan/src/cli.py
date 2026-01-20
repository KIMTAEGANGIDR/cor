"""CLI for Peraturan Crawler."""

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional, Union

import click
from rich.console import Console
from rich.table import Table

from src import __version__
from src.config import Config, config
from src.services.database import Database
from src.services.crawler import Crawler
from src.services.downloader import Downloader
from src.models.state import StateStatus
from src.utils.logging import setup_logging

console = Console()


def handle_interrupt(worker: Optional[Union[Crawler, Downloader]] = None):
    """Handle keyboard interrupt gracefully."""
    def handler(signum, frame):
        if worker:
            worker.stop()
        else:
            console.print("\n[yellow]Interrupted[/yellow]")
            sys.exit(130)
    return handler


@click.group()
@click.option("--db", type=click.Path(), default=None, help="Database path")
@click.option("--log-file", type=click.Path(), default=None, help="Log file path")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARN", "ERROR"]),
              default="INFO", help="Log level")
@click.version_option(version=__version__, prog_name="peraturan")
@click.pass_context
def main(ctx: click.Context, db: Optional[str], log_file: Optional[str], log_level: str):
    """Peraturan Crawler - Indonesian legal document crawler for peraturan.go.id."""
    ctx.ensure_object(dict)

    # Update config
    if db:
        config.db_path = Path(db)
    if log_file:
        config.log_file = Path(log_file)
    config.log_level = log_level

    # Setup logging
    setup_logging(
        level=log_level,
        log_file=config.log_file,
    )

    # Ensure directories exist
    config.ensure_directories()

    ctx.obj["config"] = config


@main.command()
@click.option("-r", "--resume", is_flag=True, help="Resume from previous state")
@click.option("-l", "--limit", type=int, default=0, help="Maximum items to crawl (0 = unlimited)")
@click.option("-t", "--type", "jenis", type=str, default=None, help="Filter by document type")
@click.option("-d", "--delay", type=float, default=1.0, help="Delay between requests (seconds)")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def crawl(ctx: click.Context, resume: bool, limit: int, jenis: Optional[str],
          delay: float, verbose: bool):
    """Crawl metadata from peraturan.go.id."""
    cfg: Config = ctx.obj["config"]
    cfg.delay = delay

    if verbose:
        setup_logging(level="DEBUG", log_file=cfg.log_file)

    crawler = Crawler(config=cfg)

    # Setup signal handler
    signal.signal(signal.SIGINT, handle_interrupt(crawler))

    console.print("[bold blue]Starting metadata crawl...[/bold blue]")

    if resume:
        console.print("[dim]Resuming from previous state[/dim]")

    try:
        state = asyncio.run(crawler.crawl(
            resume=resume,
            limit=limit,
            jenis_filter=jenis,
            show_progress=True,
        ))

        # Print summary
        console.print()
        if state.status == StateStatus.COMPLETED.value:
            console.print("[bold green]Crawl completed![/bold green]")
        elif state.status == StateStatus.PAUSED.value:
            console.print("[bold yellow]Crawl paused. Use --resume to continue.[/bold yellow]")

        console.print(f"  Completed: {state.completed_count:,}")
        console.print(f"  Failed: {state.failed_count:,}")

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@main.command()
@click.option("-r", "--resume", is_flag=True, help="Resume from previous state")
@click.option("-l", "--limit", type=int, default=0, help="Maximum PDFs to download (0 = unlimited)")
@click.option("-t", "--type", "jenis", type=str, default=None, help="Filter by document type")
@click.option("--retry-failed", is_flag=True, help="Retry previously failed downloads")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def download(ctx: click.Context, resume: bool, limit: int, jenis: Optional[str],
             retry_failed: bool, verbose: bool):
    """Download PDF files for crawled documents."""
    cfg: Config = ctx.obj["config"]

    if verbose:
        setup_logging(level="DEBUG", log_file=cfg.log_file)

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    downloader = Downloader(config=cfg)

    # Setup signal handler
    signal.signal(signal.SIGINT, handle_interrupt(downloader))

    console.print("[bold blue]Starting PDF download...[/bold blue]")

    if resume:
        console.print("[dim]Resuming from previous state[/dim]")

    try:
        state = asyncio.run(downloader.download(
            resume=resume,
            limit=limit,
            jenis_filter=jenis,
            retry_failed=retry_failed,
            show_progress=True,
        ))

        # Print summary
        console.print()
        if state.status == StateStatus.COMPLETED.value:
            console.print("[bold green]Download completed![/bold green]")
        elif state.status == StateStatus.PAUSED.value:
            console.print("[bold yellow]Download paused. Use --resume to continue.[/bold yellow]")

        console.print(f"  Downloaded: {state.completed_count:,}")
        console.print(f"  Skipped: {state.skipped_count:,}")
        console.print(f"  Failed: {state.failed_count:,}")

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@main.command()
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def status(ctx: click.Context, as_json: bool):
    """Show crawl and download status."""
    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    db = Database(config=cfg)
    db.connect()

    try:
        crawl_state = db.get_crawl_state()
        download_state = db.get_download_state()
        stats = db.get_statistics()

        if as_json:
            import json
            output = {
                "crawl": {
                    "status": crawl_state.status,
                    "progress": f"{crawl_state.completed_count}/{crawl_state.total_count}",
                    "percent": crawl_state.progress_percent,
                    "failed": crawl_state.failed_count,
                    "started_at": crawl_state.started_at,
                    "completed_at": crawl_state.completed_at,
                },
                "download": {
                    "status": download_state.status,
                    "progress": f"{download_state.completed_count}/{download_state.total_pdfs}",
                    "percent": download_state.progress_percent,
                    "skipped": download_state.skipped_count,
                    "failed": download_state.failed_count,
                },
                "database": stats,
            }
            console.print(json.dumps(output, indent=2))
        else:
            # Crawl status
            console.print("[bold]=== Crawl Status ===[/bold]")
            console.print(f"Status: {crawl_state.status}")
            console.print(
                f"Progress: {crawl_state.completed_count:,} / {crawl_state.total_count:,} "
                f"({crawl_state.progress_percent:.1f}%)"
            )
            console.print(f"Failed: {crawl_state.failed_count:,}")
            if crawl_state.started_at:
                console.print(f"Started: {crawl_state.started_at}")
            if crawl_state.completed_at:
                console.print(f"Completed: {crawl_state.completed_at}")

            console.print()

            # Download status
            console.print("[bold]=== Download Status ===[/bold]")
            console.print(f"Status: {download_state.status}")
            console.print(
                f"Progress: {download_state.completed_count:,} / {download_state.total_pdfs:,} "
                f"({download_state.progress_percent:.1f}%)"
            )
            console.print(f"Skipped: {download_state.skipped_count:,}")
            console.print(f"Failed: {download_state.failed_count:,}")

            console.print()

            # Database stats
            console.print("[bold]=== Database Stats ===[/bold]")
            console.print(f"Total laws: {stats['total']:,}")
            console.print("By type:")
            for jenis, count in stats["by_type"].items():
                console.print(f"  - {jenis}: {count:,}")
            console.print(f"With PDF: {stats['with_pdf']:,}")
            console.print(f"Failed items: {stats['failed_items']:,}")

    finally:
        db.close()


@main.command()
@click.argument("query", required=False)
@click.option("-t", "--type", "jenis", type=str, default=None, help="Filter by document type")
@click.option("-y", "--year", type=str, default=None, help="Year or range (e.g., 2024 or 2020-2024)")
@click.option("-s", "--status", "doc_status", type=click.Choice(["active", "inactive", "all"]),
              default="all", help="Filter by status")
@click.option("-l", "--limit", type=int, default=20, help="Maximum results")
@click.option("-f", "--format", "output_format", type=click.Choice(["table", "json", "csv"]),
              default="table", help="Output format")
@click.pass_context
def search(ctx: click.Context, query: Optional[str], jenis: Optional[str],
           year: Optional[str], doc_status: str, limit: int, output_format: str):
    """Search for laws in the local database."""
    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    db = Database(config=cfg)
    db.connect()

    try:
        # Build SQL query
        conditions = []
        params = []

        if query:
            conditions.append("tentang LIKE ?")
            params.append(f"%{query}%")

        if jenis:
            conditions.append("jenis = ?")
            params.append(jenis.upper())

        if year:
            if "-" in year:
                start, end = year.split("-")
                conditions.append("tahun BETWEEN ? AND ?")
                params.extend([int(start), int(end)])
            else:
                conditions.append("tahun = ?")
                params.append(int(year))

        if doc_status == "active":
            conditions.append("status = 'Berlaku'")
        elif doc_status == "inactive":
            conditions.append("status = 'Tidak Berlaku'")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT slug, jenis, nomor, tahun, tentang, status, local_pdf_path
            FROM peraturan
            WHERE {where_clause}
            ORDER BY tahun DESC, nomor ASC
            LIMIT ?
        """
        params.append(limit)

        cursor = db._connection.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            console.print("[yellow]No results found.[/yellow]")
            return

        if output_format == "json":
            import json
            results = [dict(row) for row in rows]
            output = {
                "results": results,
                "total": len(results),
                "query": query,
            }
            console.print(json.dumps(output, indent=2, ensure_ascii=False))

        elif output_format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["slug", "jenis", "nomor", "tahun", "tentang", "status", "pdf_path"])
            for row in rows:
                writer.writerow([row["slug"], row["jenis"], row["nomor"], row["tahun"],
                               row["tentang"], row["status"], row["local_pdf_path"]])
            console.print(output.getvalue())

        else:  # table
            table = Table(show_header=True, header_style="bold")
            table.add_column("Slug", style="cyan")
            table.add_column("Type")
            table.add_column("Number")
            table.add_column("Year")
            table.add_column("Title", max_width=40)

            for row in rows:
                title = row["tentang"][:37] + "..." if len(row["tentang"]) > 40 else row["tentang"]
                table.add_row(
                    row["slug"],
                    row["jenis"],
                    row["nomor"],
                    str(row["tahun"]),
                    title,
                )

            console.print(table)
            pdf_count = sum(1 for row in rows if row["local_pdf_path"])
            console.print(f"\nFound: {len(rows)} results | PDF available: {pdf_count}")

    finally:
        db.close()


@main.command()
@click.option("-f", "--format", "output_format", type=click.Choice(["json", "csv"]),
              default="json", help="Output format")
@click.option("-t", "--type", "jenis", type=str, default=None, help="Filter by document type")
@click.argument("output_file", type=click.Path())
@click.pass_context
def export(ctx: click.Context, output_format: str, jenis: Optional[str], output_file: str):
    """Export metadata to file."""
    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    db = Database(config=cfg)
    db.connect()

    try:
        peraturan_list = db.get_all_peraturan(jenis=jenis)

        if not peraturan_list:
            console.print("[yellow]No data to export.[/yellow]")
            return

        output_path = Path(output_file)

        if output_format == "json":
            import json
            data = [p.to_dict() for p in peraturan_list]
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        else:  # csv
            import csv
            fieldnames = [
                "slug", "jenis", "nomor", "tahun", "tentang", "status",
                "pdf_url", "local_pdf_path", "source_url"
            ]
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for p in peraturan_list:
                    writer.writerow(p.to_dict())

        console.print(f"[green]Exported {len(peraturan_list):,} records to {output_path}[/green]")

    finally:
        db.close()


@main.command()
@click.option("-h", "--host", default="127.0.0.1", help="Host to bind")
@click.option("-p", "--port", default=8000, help="Port to bind")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int):
    """Start web server to browse collected data."""
    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    console.print(f"[bold blue]Starting web server at http://{host}:{port}[/bold blue]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    import uvicorn
    from src.web import app
    uvicorn.run(app, host=host, port=port)


@main.command()
@click.option("--fix", is_flag=True, help="Fix invalid PDFs and clear failed items")
@click.option("--retry", is_flag=True, help="Retry failed items")
@click.option("--check-urls", is_flag=True, help="Validate all URL paths before crawling")
@click.option("-v", "--verbose", is_flag=True, help="Show all details")
@click.pass_context
def validate(ctx: click.Context, fix: bool, retry: bool, check_urls: bool, verbose: bool):
    """Validate system status: PDFs, failed crawls, and failed downloads.

    Checks:
    - PDF files for corruption (HTML error pages saved as PDF)
    - Failed crawl items and their error messages
    - Failed download items and their error messages
    - URL paths (with --check-urls flag)

    Examples:
        peraturan validate           # Show validation report
        peraturan validate --fix     # Fix invalid PDFs
        peraturan validate --retry   # Clear failed items for retry
        peraturan validate --check-urls  # Validate URL paths
        peraturan validate -v        # Show detailed list
    """
    # Handle URL validation first (doesn't require database)
    if check_urls:
        console.print("[bold blue]=== URL Validation ===[/bold blue]")
        import asyncio
        from src.services.validator import validate_urls

        all_valid, results = asyncio.run(validate_urls())

        for jenis, result in results.items():
            status_icon = "[green]✓[/green]" if result["is_valid"] else "[red]✗[/red]"
            redirect = f" -> {result['redirect_url']}" if result.get("redirect_url") else ""
            error = f" ({result['error']})" if result.get("error") else ""
            console.print(f"  {status_icon} {jenis}: {result['url']}{redirect}{error}")

        if all_valid:
            console.print("[green]All URLs are valid![/green]")
        else:
            console.print("[red]Some URLs are invalid. Check the output above.[/red]")

        console.print()
    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    db = Database(config=cfg)
    db.connect()

    try:
        # === 1. Check Invalid PDF Files ===
        console.print("[bold blue]=== PDF Validation ===[/bold blue]")

        pdf_dir = cfg.data_dir / "pdfs"
        invalid_files = []
        total_checked = 0

        if pdf_dir.exists():
            for pdf_path in pdf_dir.rglob("*.pdf"):
                total_checked += 1
                try:
                    with open(pdf_path, "rb") as f:
                        header = f.read(8)
                        if not header.startswith(b"%PDF"):
                            is_html = header.startswith(b"<!DOC") or header.startswith(b"<html")
                            invalid_files.append({
                                "path": pdf_path,
                                "slug": pdf_path.stem,
                                "type": "HTML error page" if is_html else "Unknown format",
                                "size": pdf_path.stat().st_size,
                            })
                except Exception as e:
                    invalid_files.append({
                        "path": pdf_path,
                        "slug": pdf_path.stem,
                        "type": f"Read error: {e}",
                        "size": 0,
                    })

        console.print(f"  PDF files checked: {total_checked}")
        if invalid_files:
            console.print(f"  [red]Invalid PDFs: {len(invalid_files)}[/red]")
            if verbose:
                for item in invalid_files[:20]:
                    console.print(f"    - {item['slug']} ({item['type']})")
        else:
            console.print(f"  [green]All PDFs valid[/green]")

        # === 2. Check Failed Items ===
        console.print("\n[bold blue]=== Failed Items ===[/bold blue]")

        cursor = db._connection.cursor()

        # Get failed items summary by type
        cursor.execute("""
            SELECT item_type, COUNT(*) as cnt
            FROM failed_items
            GROUP BY item_type
        """)
        failed_summary = cursor.fetchall()

        if failed_summary:
            for row in failed_summary:
                console.print(f"  [red]{row['item_type']}: {row['cnt']} failed[/red]")

            # Get error breakdown
            cursor.execute("""
                SELECT item_type, error_message, COUNT(*) as cnt
                FROM failed_items
                GROUP BY item_type, error_message
                ORDER BY cnt DESC
                LIMIT 10
            """)
            error_breakdown = cursor.fetchall()

            if verbose and error_breakdown:
                console.print("\n  [bold]Error breakdown:[/bold]")
                for row in error_breakdown:
                    msg = row['error_message'][:60] + "..." if len(row['error_message'] or "") > 60 else row['error_message']
                    console.print(f"    [{row['item_type']}] {row['cnt']}x: {msg}")
        else:
            console.print("  [green]No failed items[/green]")

        # === 3. Check Download State ===
        console.print("\n[bold blue]=== Download Status ===[/bold blue]")
        cursor.execute("SELECT * FROM download_state WHERE id = 1")
        ds = cursor.fetchone()
        if ds:
            console.print(f"  Completed: {ds['completed_count']}")
            console.print(f"  Skipped: {ds['skipped_count']}")
            if ds['failed_count'] > 0:
                console.print(f"  [red]Failed: {ds['failed_count']}[/red]")
            else:
                console.print(f"  [green]Failed: 0[/green]")

        # === 4. Check Crawl Progress ===
        console.print("\n[bold blue]=== Crawl Progress ===[/bold blue]")
        cursor.execute("""
            SELECT jenis, COUNT(*) as cnt
            FROM peraturan
            GROUP BY jenis
            ORDER BY cnt DESC
        """)
        crawl_progress = cursor.fetchall()

        # Known totals (approximate)
        known_totals = {
            "UNDANG-UNDANG": 1907,
            "PERPPU": 218,
            "PERATURAN PEMERINTAH": 4972,
            "PERATURAN PRESIDEN": 2618,
            "PERATURAN MENTERI": 19723,
            "PERATURAN BADAN/LEMBAGA": 6614,
            "PERATURAN DAERAH": 5000,  # estimate
        }

        for row in crawl_progress:
            jenis = row['jenis']
            cnt = row['cnt']
            total = known_totals.get(jenis, "?")
            if isinstance(total, int) and cnt >= total * 0.95:
                status = "[green]✓[/green]"
            else:
                status = f"[yellow]({cnt}/{total})[/yellow]"
            console.print(f"  {jenis}: {cnt:,} {status}")

        cursor.close()

        # === Fix Actions ===
        if fix and invalid_files:
            console.print("\n[bold yellow]Fixing invalid PDFs...[/bold yellow]")
            fixed_count = 0
            for item in invalid_files:
                try:
                    item["path"].unlink()
                except Exception:
                    pass
                cursor = db._connection.cursor()
                cursor.execute(
                    "UPDATE peraturan SET local_pdf_path = NULL WHERE slug = ?",
                    (item["slug"],)
                )
                db._connection.commit()
                cursor.close()
                fixed_count += 1
            console.print(f"[green]Fixed {fixed_count} invalid PDFs[/green]")

        if retry:
            console.print("\n[bold yellow]Clearing failed items for retry...[/bold yellow]")
            cursor = db._connection.cursor()
            cursor.execute("DELETE FROM failed_items")
            cursor.execute("UPDATE download_state SET failed_count = 0 WHERE id = 1")
            cursor.execute("UPDATE crawl_state SET failed_count = 0 WHERE id = 1")
            db._connection.commit()
            cursor.close()
            console.print("[green]Failed items cleared. They will be retried next run.[/green]")

        if not fix and not retry and (invalid_files or failed_summary):
            console.print("\n[dim]Use --fix to clean invalid PDFs, --retry to clear failed items[/dim]")

    finally:
        db.close()


@main.command()
@click.option("-j", "--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def health(ctx: click.Context, as_json: bool):
    """Show crawler health status and monitoring information.

    Displays:
    - Current health status
    - Last activity timestamp
    - Error statistics
    - Recovery attempts

    Examples:
        peraturan health         # Show health status
        peraturan health --json  # Output as JSON
    """
    import json
    from src.services.health import HealthMonitor, HealthStatus

    cfg: Config = ctx.obj["config"]

    # Create a health monitor to check status
    monitor = HealthMonitor()

    # Check if database exists for additional stats
    db_stats = None
    if cfg.db_path.exists():
        db = Database(config=cfg)
        db.connect()
        try:
            db_stats = db.get_statistics()
            crawl_state = db.get_crawl_state()
            download_state = db.get_download_state()
        finally:
            db.close()

    # Build health report
    report = {
        "status": "healthy",
        "database": {
            "exists": cfg.db_path.exists(),
            "path": str(cfg.db_path),
        },
    }

    if db_stats:
        report["crawl"] = {
            "total_items": db_stats["total"],
            "by_type": db_stats["by_type"],
            "with_pdf": db_stats["with_pdf"],
            "failed_items": db_stats["failed_items"],
        }
        report["crawl_state"] = {
            "status": crawl_state.status,
            "completed": crawl_state.completed_count,
            "failed": crawl_state.failed_count,
        }
        report["download_state"] = {
            "status": download_state.status,
            "completed": download_state.completed_count,
            "skipped": download_state.skipped_count,
            "failed": download_state.failed_count,
        }

    if as_json:
        console.print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    # Display formatted output
    console.print("[bold blue]=== Crawler Health Status ===[/bold blue]")
    console.print()

    # Database status
    if cfg.db_path.exists():
        console.print(f"[green]✓[/green] Database: {cfg.db_path}")
    else:
        console.print(f"[yellow]![/yellow] Database: Not found")

    if db_stats:
        console.print()
        console.print("[bold]Crawl Statistics:[/bold]")
        console.print(f"  Total items: {db_stats['total']:,}")
        console.print(f"  With PDF: {db_stats['with_pdf']:,}")
        console.print(f"  Failed items: {db_stats['failed_items']}")

        console.print()
        console.print("[bold]By Type:[/bold]")
        for jenis, count in sorted(db_stats["by_type"].items(), key=lambda x: -x[1]):
            console.print(f"  {jenis}: {count:,}")

        console.print()
        console.print("[bold]Crawl State:[/bold]")
        status_icon = "[green]✓[/green]" if crawl_state.status in ["completed", "running"] else "[yellow]![/yellow]"
        console.print(f"  {status_icon} Status: {crawl_state.status}")
        console.print(f"  Completed: {crawl_state.completed_count:,}")
        console.print(f"  Failed: {crawl_state.failed_count}")

        console.print()
        console.print("[bold]Download State:[/bold]")
        status_icon = "[green]✓[/green]" if download_state.status in ["completed", "running"] else "[yellow]![/yellow]"
        console.print(f"  {status_icon} Status: {download_state.status}")
        console.print(f"  Completed: {download_state.completed_count:,}")
        console.print(f"  Skipped: {download_state.skipped_count:,}")
        console.print(f"  Failed: {download_state.failed_count}")


@main.command()
@click.option("-r", "--refresh", default=5, type=int, help="Refresh interval in seconds")
@click.option("-j", "--json", "as_json", is_flag=True, help="Output single snapshot as JSON")
@click.pass_context
def dashboard(ctx: click.Context, refresh: int, as_json: bool):
    """Show real-time progress dashboard.

    Displays:
    - Overall crawl progress by document type
    - Items processed per hour
    - Estimated time to completion
    - Health status

    Examples:
        peraturan dashboard            # Live dashboard (5s refresh)
        peraturan dashboard -r 10      # Refresh every 10 seconds
        peraturan dashboard --json     # Single snapshot as JSON
    """
    import json
    from src.services.dashboard import Dashboard

    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    dash = Dashboard(config=cfg)

    if as_json:
        stats = dash.get_stats()
        console.print(json.dumps(stats.to_dict(), indent=2, ensure_ascii=False))
        return

    # Run live dashboard
    console.print("[bold blue]Starting dashboard...[/bold blue]")
    console.print("[dim]Press Ctrl+C to exit[/dim]")
    console.print()

    asyncio.run(dash.run_live(refresh_seconds=refresh))


@main.command()
@click.option("-m", "--mode", type=click.Choice(["auto", "initial", "maintenance"]),
              default="auto", help="Scheduler mode: auto (switch by completion), initial (fast), maintenance (slow)")
@click.option("-i", "--interval", default=None, type=int, help="Override interval between runs (minutes)")
@click.option("-b", "--batch", default=None, type=int, help="Override items to process per run")
@click.option("--no-download", is_flag=True, help="Skip PDF download")
@click.option("--once", is_flag=True, help="Run once and exit")
@click.option("-t", "--type", "jenis", type=str, default=None, help="Specific type to crawl (for --once)")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def schedule(ctx: click.Context, mode: str, interval: Optional[int], batch: Optional[int],
             no_download: bool, once: bool, jenis: Optional[str], verbose: bool):
    """Run scheduled crawler that rotates through document types.

    This command runs a continuous crawler that:
    - Rotates through all document types (UU, PP, Perpres, etc.)
    - Crawls a batch of items every interval
    - Optionally downloads PDFs for crawled items
    - Saves state to resume from where it left off

    Modes:
        auto        - Automatically switch between initial and maintenance (default)
        initial     - Aggressive mode: 5min interval, 200 batch, for fast collection
        maintenance - Conservative mode: 60min interval, 50 batch, for updates

    Examples:
        peraturan schedule                    # Auto mode (recommended)
        peraturan schedule --mode initial     # Fast initial collection
        peraturan schedule --mode maintenance # Slow maintenance updates
        peraturan schedule -i 5 -b 100        # Custom: every 5 min, 100 items
        peraturan schedule --once             # Run one cycle and exit
        peraturan schedule --once -t "UNDANG-UNDANG"  # Crawl specific type once
    """
    from src.services.scheduler import Scheduler, SchedulerMode

    cfg: Config = ctx.obj["config"]

    if verbose:
        setup_logging(level="DEBUG", log_file=cfg.log_file)

    # Map mode string to enum
    mode_map = {
        "auto": SchedulerMode.AUTO,
        "initial": SchedulerMode.INITIAL,
        "maintenance": SchedulerMode.MAINTENANCE,
    }
    scheduler_mode = mode_map[mode]

    scheduler = Scheduler(
        config=cfg,
        mode=scheduler_mode,
        interval_minutes=interval,
        batch_size=batch,
        download_pdfs=not no_download,
    )

    # Setup signal handler
    def handle_stop(signum, frame):
        console.print("\n[yellow]Stopping scheduler...[/yellow]")
        scheduler.stop()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    # Show initial status
    status = scheduler.get_status()
    console.print("[bold blue]Scheduled Crawler[/bold blue]")
    console.print(f"  Mode: {status['mode']} (effective: {status['effective_mode']})")
    console.print(f"  Completion: {status['completion_percent']:.1f}%")
    console.print(f"  Interval: {status['interval_minutes']} minutes")
    console.print(f"  Batch size: {status['batch_size']} items")
    console.print(f"  Request delay: {status['effective_delay']}s")
    console.print(f"  Download PDFs: {not no_download}")
    console.print(f"  Total runs: {status['run_count']}")
    console.print(f"  Total crawled: {status['total_crawled']:,}")
    console.print(f"  Total downloaded: {status['total_downloaded']:,}")
    if status['mode_switch_count'] > 0:
        console.print(f"  Mode switches: {status['mode_switch_count']}")
    console.print()

    if once:
        # Run once and exit
        console.print(f"[dim]Running single cycle for: {jenis or status['current_type']}[/dim]")
        results = asyncio.run(scheduler.run_once(jenis=jenis))

        console.print()
        console.print(f"[green]Completed:[/green] {results['type']}")
        console.print(f"  Crawled: {results['crawled']}")
        console.print(f"  Downloaded: {results['downloaded']}")
        if results['errors']:
            console.print(f"  [red]Errors: {len(results['errors'])}[/red]")
    else:
        # Callback to show progress
        def on_cycle_complete(results, state):
            console.print()
            console.print(f"[green]Cycle #{state.run_count} completed:[/green] {results['type']}")
            console.print(f"  Crawled: {results['crawled']} | Downloaded: {results['downloaded']}")
            console.print(f"  Total: {state.total_crawled:,} crawled, {state.total_downloaded:,} downloaded")
            if results['errors']:
                console.print(f"  [red]Errors: {len(results['errors'])}[/red]")

        # Run continuously
        console.print(f"[dim]Starting with: {status['current_type']}[/dim]")
        console.print("[dim]Press Ctrl+C to stop[/dim]")
        console.print()

        asyncio.run(scheduler.run(callback=on_cycle_complete))

        console.print("[bold green]Scheduler stopped.[/bold green]")


@main.command()
@click.option("-o", "--output-dir", type=click.Path(), default="docs/exports/koica",
              help="Output directory for reports")
@click.option("-f", "--format", "output_format", type=click.Choice(["json", "md", "docx", "all"]),
              default="all", help="Output format")
@click.option("--bpk-db", type=click.Path(exists=False), default=None,
              help="Path to BPK database for comparison")
@click.pass_context
def koica_report(ctx: click.Context, output_dir: str, output_format: str, bpk_db: Optional[str]):
    """Generate KOICA pre-analysis data status report.

    Generates comprehensive analysis reports for the KOICA Indonesian Legal
    Information System project. Output formats include JSON, Markdown, and Word (DOCX).

    Examples:
        peraturan koica-report                     # Generate all formats
        peraturan koica-report --format json       # JSON only
        peraturan koica-report --format md         # Markdown only
        peraturan koica-report --format docx       # Word document only
        peraturan koica-report -o ./reports        # Custom output directory
    """
    import json
    from datetime import datetime

    cfg: Config = ctx.obj["config"]

    if not cfg.db_path.exists():
        console.print("[yellow]No database found. Run 'peraturan crawl' first.[/yellow]")
        return

    console.print("[bold blue]KOICA Pre-analysis Report Generator[/bold blue]")
    console.print(f"  Database: {cfg.db_path}")
    console.print(f"  Output: {output_dir}")
    console.print(f"  Format: {output_format}")
    console.print()

    try:
        from src.services.koica_analyzer import KoicaAnalyzer
    except ImportError:
        console.print("[red]Error: KoicaAnalyzer module not found[/red]")
        return

    # Check for BPK database
    bpk_db_path = None
    if bpk_db:
        bpk_db_path = Path(bpk_db)
        if not bpk_db_path.exists():
            console.print(f"[yellow]BPK database not found: {bpk_db}[/yellow]")
            bpk_db_path = None
    else:
        # Try default location
        default_bpk = Path("bpk/data/peraturan_bpk.db")
        if default_bpk.exists():
            bpk_db_path = default_bpk
            console.print(f"  BPK DB: {bpk_db_path}")

    console.print()
    console.print("[dim]Running analysis...[/dim]")

    try:
        # Run analysis
        analyzer = KoicaAnalyzer(cfg.db_path, bpk_db_path)
        results = analyzer.run_full_analysis()

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        generated_files = []

        # Generate JSON reports
        if output_format in ("json", "all"):
            json_dir = output_path / "data" / "koica_analysis"
            json_dir.mkdir(parents=True, exist_ok=True)

            # Individual section files
            sections = {
                "document_types": "01_document_types.json",
                "year_distribution": "02_year_distribution.json",
                "agency_distribution": "03_agency_distribution.json",
                "data_completeness": "04_data_completeness.json",
                "pdf_availability": "05_pdf_availability.json",
                "legal_status": "06_legal_status.json",
                "bpk_comparison": "07_bpk_comparison.json",
            }

            for section_name, filename in sections.items():
                if section_name in results:
                    file_path = json_dir / filename
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(results[section_name], f, ensure_ascii=False, indent=2)
                    generated_files.append(str(file_path))

            # Summary file
            summary_path = json_dir / "summary.json"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            generated_files.append(str(summary_path))

            console.print(f"[green]✓[/green] Generated {len(generated_files)} JSON files")

        # Generate Markdown report
        if output_format in ("md", "all"):
            md_path = output_path / "KOICA_사전분석_데이터현황보고서.md"
            _generate_markdown_cli(results, md_path)
            generated_files.append(str(md_path))
            console.print(f"[green]✓[/green] Generated Markdown: {md_path.name}")

        # Generate DOCX report
        if output_format in ("docx", "all"):
            try:
                from docx import Document
                docx_path = output_path / "KOICA_사전분석_데이터현황보고서.docx"
                _generate_docx_cli(results, docx_path)
                generated_files.append(str(docx_path))
                console.print(f"[green]✓[/green] Generated DOCX: {docx_path.name}")
            except ImportError:
                console.print("[yellow]![/yellow] DOCX skipped (python-docx not installed)")

        console.print()
        console.print("[bold green]Report generation complete![/bold green]")
        console.print()

        # Show key findings
        console.print("[bold]Key Findings:[/bold]")
        for finding in results['summary'].get('key_findings', [])[:5]:
            console.print(f"  • {finding}")

        console.print()
        console.print(f"[dim]Output directory: {output_path.absolute()}[/dim]")

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _generate_markdown_cli(results: dict, output_path: Path) -> None:
    """Generate Markdown report from CLI."""
    from src.services.koica_analyzer import JENIS_KOREAN

    lines = []

    # Title
    lines.append("# KOICA 사전분석: 인도네시아 법령 데이터 현황 보고서")
    lines.append("")
    lines.append(f"**분석일시:** {results['meta']['analysis_date']}")
    lines.append("")

    # Summary
    lines.append("## 1. 개요")
    lines.append("")
    lines.append("### 핵심 지표 요약")
    lines.append("")
    for finding in results['summary'].get('key_findings', []):
        lines.append(f"- {finding}")
    lines.append("")

    # Document Types
    lines.append("## 2. 법령 유형별 분포")
    lines.append("")
    lines.append("| 유형 | 건수 | 비율 | 유효율 |")
    lines.append("|------|-----|------|-------|")
    for t in results['document_types']['types']:
        lines.append(f"| {t['jenis_korean']} | {t['total']:,} | {t['percentage']}% | {t['berlaku_rate']}% |")
    lines.append("")

    # Year Distribution
    lines.append("## 3. 연도별 분포")
    lines.append("")
    year_data = results['year_distribution']
    lines.append(f"- 연도 범위: {year_data['year_range']['min']}년 ~ {year_data['year_range']['max']}년")
    lines.append("")
    lines.append("### 10년 단위")
    lines.append("")
    lines.append("| 기간 | 건수 |")
    lines.append("|------|-----|")
    for d in year_data['decade_summary']:
        lines.append(f"| {d['decade']} | {d['total']:,} |")
    lines.append("")

    # Data Completeness
    lines.append("## 4. 데이터 완성도")
    lines.append("")
    comp_data = results['data_completeness']
    lines.append(f"전체 완성도: **{comp_data['overall_completeness_pct']}%**")
    lines.append("")

    # PDF Availability
    lines.append("## 5. PDF 가용성")
    lines.append("")
    pdf_data = results['pdf_availability']
    lines.append(f"- 다운로드 완료: {pdf_data['downloaded']:,}건 ({pdf_data['overall_download_pct']}%)")
    lines.append(f"- 총 용량: {pdf_data['storage']['total_size_gb']}GB")
    lines.append("")

    # Legal Status
    lines.append("## 6. 현행성 상태")
    lines.append("")
    status_data = results['legal_status']
    lines.append(f"- 현행 (Berlaku): {status_data['berlaku']:,}건 ({status_data['berlaku_pct']}%)")
    lines.append(f"- 폐지 (Tidak Berlaku): {status_data['tidak_berlaku']:,}건 ({status_data['tidak_berlaku_pct']}%)")
    lines.append("")

    # BPK Comparison
    if results['bpk_comparison'].get('available'):
        lines.append("## 7. BPK 비교")
        lines.append("")
        bpk_data = results['bpk_comparison']
        lines.append(f"- 법제처: {bpk_data['peraturan_total']:,}건")
        lines.append(f"- BPK: {bpk_data['bpk_total']:,}건")
        lines.append("")

    lines.append("---")
    lines.append(f"*자동 생성: {results['meta']['analysis_date']}*")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _generate_docx_cli(results: dict, output_path: Path) -> None:
    """Generate DOCX report from CLI."""
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from src.services.koica_analyzer import JENIS_KOREAN

    doc = Document()

    # Title
    title = doc.add_heading("KOICA 사전분석: 인도네시아 법령 데이터 현황 보고서", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"분석일시: {results['meta']['analysis_date']}")
    doc.add_paragraph("")

    # Key Findings
    doc.add_heading("핵심 지표 요약", level=1)
    for finding in results['summary'].get('key_findings', []):
        doc.add_paragraph(finding, style="List Bullet")

    # Document Types
    doc.add_heading("법령 유형별 분포", level=1)
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "유형", "건수", "비율", "유효율"

    for t in results['document_types']['types']:
        row = table.add_row().cells
        row[0].text = t['jenis_korean']
        row[1].text = f"{t['total']:,}"
        row[2].text = f"{t['percentage']}%"
        row[3].text = f"{t['berlaku_rate']}%"

    # Year Distribution
    doc.add_heading("연도별 분포", level=1)
    year_data = results['year_distribution']
    doc.add_paragraph(f"연도 범위: {year_data['year_range']['min']}년 ~ {year_data['year_range']['max']}년")

    # Data Completeness
    doc.add_heading("데이터 완성도", level=1)
    comp_data = results['data_completeness']
    doc.add_paragraph(f"전체 완성도: {comp_data['overall_completeness_pct']}%")

    # PDF Availability
    doc.add_heading("PDF 가용성", level=1)
    pdf_data = results['pdf_availability']
    doc.add_paragraph(f"다운로드 완료: {pdf_data['downloaded']:,}건 ({pdf_data['overall_download_pct']}%)")

    # Legal Status
    doc.add_heading("현행성 상태", level=1)
    status_data = results['legal_status']
    doc.add_paragraph(f"현행: {status_data['berlaku']:,}건 ({status_data['berlaku_pct']}%)")
    doc.add_paragraph(f"폐지: {status_data['tidak_berlaku']:,}건 ({status_data['tidak_berlaku_pct']}%)")

    # BPK Comparison
    if results['bpk_comparison'].get('available'):
        doc.add_heading("BPK 비교", level=1)
        bpk_data = results['bpk_comparison']
        doc.add_paragraph(f"법제처: {bpk_data['peraturan_total']:,}건")
        doc.add_paragraph(f"BPK: {bpk_data['bpk_total']:,}건")

    doc.save(str(output_path))


@main.command()
@click.pass_context
def crawl_uud(ctx: click.Context):
    """Crawl UUD 1945 page (constitution with multiple PDF versions).

    UUD 1945 has a special structure with multiple PDFs for the original
    constitution and its amendments. This command handles that special case.

    Examples:
        peraturan crawl-uud  # Crawl UUD 1945 and amendments
    """
    cfg: Config = ctx.obj["config"]

    console.print("[bold blue]Crawling UUD 1945...[/bold blue]")

    try:
        crawler = Crawler(config=cfg)
        added = asyncio.run(crawler.crawl_uud())

        console.print()
        console.print(f"[bold green]UUD crawl complete![/bold green]")
        console.print(f"  PDF attachments added: {added}")

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@main.command()
@click.option("-l", "--limit", type=int, default=0, help="Maximum attachments to download (0 = unlimited)")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.pass_context
def download_attachments(ctx: click.Context, limit: int, verbose: bool):
    """Download attachment PDFs (e.g., UUD amendments).

    Downloads PDF attachments that have been added for documents with
    multiple PDFs (like UUD 1945 and its amendments).

    Examples:
        peraturan download-attachments       # Download all attachments
        peraturan download-attachments -l 10 # Download up to 10
    """
    cfg: Config = ctx.obj["config"]

    if verbose:
        setup_logging(level="DEBUG", log_file=cfg.log_file)

    console.print("[bold blue]Downloading attachments...[/bold blue]")

    try:
        downloader = Downloader(config=cfg)
        stats = asyncio.run(downloader.download_attachments(limit=limit))

        console.print()
        console.print(f"[bold green]Download complete![/bold green]")
        console.print(f"  Total: {stats['total']}")
        console.print(f"  Downloaded: {stats['downloaded']}")
        console.print(f"  Failed: {stats['failed']}")

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)


@main.command()
@click.option("--public-only", is_flag=True, default=True, help="Only crawl publicly available info")
@click.option("-u", "--username", type=str, default=None, help="Login username")
@click.option("-p", "--password", type=str, default=None, help="Login password")
@click.option("-l", "--limit", type=int, default=0, help="Maximum translations to crawl")
@click.pass_context
def crawl_translations(ctx: click.Context, public_only: bool, username: Optional[str],
                       password: Optional[str], limit: int):
    """Crawl official translations from e-penerjemahan.peraturan.go.id.

    The translation system requires authentication. Use --public-only (default)
    to crawl only publicly available translation information.

    Note: Full translation crawling requires login credentials.

    Examples:
        peraturan crawl-translations             # Public info only
        peraturan crawl-translations -u user -p pass  # Authenticated crawl
    """
    from src.services.translation_crawler import run_translation_crawler

    console.print("[bold blue]Crawling translations...[/bold blue]")

    try:
        if not public_only and (not username or not password):
            console.print("[yellow]Username and password required for authenticated crawl[/yellow]")
            console.print("[dim]Use --public-only for public information only[/dim]")
            return

        results = asyncio.run(run_translation_crawler(
            username=username,
            password=password,
            limit=limit,
            public_only=public_only,
        ))

        console.print()
        console.print(f"[bold green]Translation crawl complete![/bold green]")
        console.print(f"  Mode: {results['mode']}")

        if results['mode'] == 'public':
            console.print(f"  Found: {results['found']} translations")
            if results.get('translations'):
                console.print("\n  Sample translations:")
                for t in results['translations'][:5]:
                    console.print(f"    - {t.get('title', t.get('url', 'Unknown'))}")
        else:
            console.print(f"  Crawled: {results['crawled']} translations")

    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
