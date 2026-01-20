#!/usr/bin/env python3
"""
ILIS OCR Worker - GPU Server
헤더 이미지 OCR 처리 워커

Usage:
    python worker.py status              # 상태 확인
    python worker.py run --limit 10      # 10개만 테스트
    python worker.py run                 # 전체 처리
    python worker.py run --category uu   # UU만 처리
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

console = Console()

# Config
try:
    from config import BASE_DIR, HEADERS_DIR, DB_PATH, OCR_LANGUAGES, GPU_ENABLED, BATCH_SIZE, CATEGORIES
except ImportError:
    # Fallback for direct execution
    BASE_DIR = Path("/mnt/workspace/images")
    HEADERS_DIR = BASE_DIR / "headers"
    DB_PATH = BASE_DIR / "ocr_pipeline.db"
    OCR_LANGUAGES = ['id', 'en']
    GPU_ENABLED = True
    BATCH_SIZE = 100
    CATEGORIES = ['uu', 'pp', 'perpres', 'permen', 'other']


# Global OCR reader (lazy load)
_reader = None


def get_reader():
    """Get or create EasyOCR reader (singleton)"""
    global _reader
    if _reader is None:
        import easyocr
        console.print("[cyan]Loading EasyOCR model...[/cyan]")
        _reader = easyocr.Reader(OCR_LANGUAGES, gpu=GPU_ENABLED)
        console.print("[green]EasyOCR ready![/green]")
    return _reader


def get_db_connection():
    """Get SQLite connection"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_pending_headers(conn, category: str = None, limit: int = None):
    """Get pending headers from DB"""
    query = """
        SELECT h.id, h.document_id, h.image_path
        FROM headers h
        WHERE h.status = 'pending'
    """
    params = []

    if category:
        query += " AND h.image_path LIKE ?"
        params.append(f"%/{category}/%")

    query += " ORDER BY h.id"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    cursor = conn.execute(query, params)
    return cursor.fetchall()


def process_image(image_path: str) -> dict:
    """Process single image with EasyOCR"""
    reader = get_reader()

    try:
        # Run OCR
        results = reader.readtext(image_path)

        # Extract text and calculate confidence
        lines = []
        confidences = []

        for bbox, text, conf in results:
            lines.append(text)
            confidences.append(conf)

        raw_text = '\n'.join(lines)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0

        return {
            'raw_text': raw_text,
            'lines': lines,
            'confidence': avg_confidence,
            'status': 'extracted',
            'error': None
        }
    except Exception as e:
        return {
            'raw_text': None,
            'lines': [],
            'confidence': 0,
            'status': 'error',
            'error': str(e)
        }


def update_header(conn, header_id: int, result: dict):
    """Update header record with OCR result"""
    conn.execute("""
        UPDATE headers
        SET raw_text = ?,
            lines = ?,
            ocr_confidence = ?,
            status = ?,
            error_message = ?
        WHERE id = ?
    """, (
        result['raw_text'],
        json.dumps(result['lines'], ensure_ascii=False) if result['lines'] else None,
        result['confidence'],
        result['status'],
        result['error'],
        header_id
    ))
    conn.commit()


@click.group()
def cli():
    """ILIS OCR Worker - GPU Server"""
    pass


@cli.command()
def status():
    """Show current OCR status"""
    if not DB_PATH.exists():
        console.print("[red]Database not found![/red]")
        console.print(f"Expected: {DB_PATH}")
        return

    conn = get_db_connection()

    # Overall stats
    cursor = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'extracted' THEN 1 ELSE 0 END) as extracted,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error
        FROM headers
    """)
    stats = cursor.fetchone()

    table = Table(title="OCR Status")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Percentage", justify="right")

    total = stats['total'] or 0
    pending = stats['pending'] or 0
    extracted = stats['extracted'] or 0
    error = stats['error'] or 0

    if total > 0:
        table.add_row("Total", str(total), "100%")
        table.add_row("Pending", str(pending), f"{pending/total*100:.1f}%")
        table.add_row("Extracted", str(extracted), f"{extracted/total*100:.1f}%")
        table.add_row("Error", str(error), f"{error/total*100:.1f}%")
    else:
        table.add_row("Total", "0", "N/A")

    console.print(table)

    # Per category
    console.print("\n[bold]By Category:[/bold]")
    cat_table = Table()
    cat_table.add_column("Category")
    cat_table.add_column("Pending", justify="right")
    cat_table.add_column("Extracted", justify="right")
    cat_table.add_column("Error", justify="right")

    for cat in CATEGORIES:
        cursor = conn.execute("""
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'extracted' THEN 1 ELSE 0 END) as extracted,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error
            FROM headers
            WHERE image_path LIKE ?
        """, (f"%/{cat}/%",))
        cat_stats = cursor.fetchone()
        cat_table.add_row(
            cat.upper(),
            str(cat_stats['pending'] or 0),
            str(cat_stats['extracted'] or 0),
            str(cat_stats['error'] or 0)
        )

    console.print(cat_table)
    conn.close()


@cli.command()
@click.option('--category', '-c', type=click.Choice(CATEGORIES), help='Process specific category only')
@click.option('--limit', '-l', type=int, help='Limit number of images to process')
@click.option('--batch-size', '-b', type=int, default=BATCH_SIZE, help='Batch size for progress updates')
@click.option('--dry-run', is_flag=True, help='Show what would be processed without running OCR')
def run(category, limit, batch_size, dry_run):
    """Run OCR processing"""
    if not DB_PATH.exists():
        console.print("[red]Database not found![/red]")
        return

    conn = get_db_connection()
    headers = get_pending_headers(conn, category, limit)

    if not headers:
        console.print("[green]No pending headers to process![/green]")
        return

    total = len(headers)
    console.print(f"[cyan]Found {total} pending headers[/cyan]")

    if dry_run:
        console.print("[yellow]Dry run - showing first 10:[/yellow]")
        for h in headers[:10]:
            console.print(f"  - {h['document_id']}: {h['image_path']}")
        return

    # Pre-load OCR model
    get_reader()

    # Process with progress
    processed = 0
    errors = 0
    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Processing...", total=total)

        for header in headers:
            image_path = header['image_path']

            # Convert relative path if needed
            if not Path(image_path).is_absolute():
                image_path = str(HEADERS_DIR / image_path.lstrip('/'))

            # Check file exists
            if not Path(image_path).exists():
                update_header(conn, header['id'], {
                    'raw_text': None,
                    'lines': [],
                    'confidence': 0,
                    'status': 'error',
                    'error': f'File not found: {image_path}'
                })
                errors += 1
            else:
                result = process_image(image_path)
                update_header(conn, header['id'], result)
                if result['status'] == 'error':
                    errors += 1

            processed += 1
            progress.update(task, advance=1, description=f"Processing {header['document_id'][:30]}...")

            # Log progress every batch_size
            if processed % batch_size == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed
                eta = (total - processed) / rate if rate > 0 else 0
                console.print(f"[dim]Processed {processed}/{total} ({rate:.1f}/sec, ETA: {eta/60:.1f}min)[/dim]")

    # Summary
    elapsed = time.time() - start_time
    console.print()
    console.print("[bold green]Processing Complete![/bold green]")
    console.print(f"  Total: {processed}")
    console.print(f"  Errors: {errors}")
    console.print(f"  Time: {elapsed/60:.1f} minutes")
    console.print(f"  Rate: {processed/elapsed:.2f} images/sec")

    conn.close()


@cli.command()
@click.option('--limit', '-l', type=int, default=10, help='Number of errors to retry')
def retry_errors(limit):
    """Retry failed OCR processing"""
    conn = get_db_connection()

    # Reset error status to pending
    conn.execute("""
        UPDATE headers
        SET status = 'pending', error_message = NULL
        WHERE status = 'error'
        AND id IN (SELECT id FROM headers WHERE status = 'error' LIMIT ?)
    """, (limit,))

    affected = conn.total_changes
    conn.commit()

    console.print(f"[green]Reset {affected} error records to pending[/green]")
    console.print("Run 'python worker.py run' to reprocess")

    conn.close()


@cli.command()
def test():
    """Test OCR with a sample image"""
    # Find first image
    sample = None
    for cat in CATEGORIES:
        cat_dir = HEADERS_DIR / cat
        if cat_dir.exists():
            images = list(cat_dir.glob("*.jpg"))
            if images:
                sample = images[0]
                break

    if not sample:
        console.print("[red]No sample image found![/red]")
        return

    console.print(f"[cyan]Testing with: {sample.name}[/cyan]")

    result = process_image(str(sample))

    console.print("\n[bold]Result:[/bold]")
    console.print(f"Status: {result['status']}")
    console.print(f"Confidence: {result['confidence']:.2f}")
    console.print(f"Lines: {len(result['lines'])}")

    if result['raw_text']:
        console.print("\n[bold]Extracted Text:[/bold]")
        console.print(result['raw_text'][:500])
        if len(result['raw_text']) > 500:
            console.print("...")

    if result['error']:
        console.print(f"\n[red]Error: {result['error']}[/red]")


if __name__ == '__main__':
    cli()
