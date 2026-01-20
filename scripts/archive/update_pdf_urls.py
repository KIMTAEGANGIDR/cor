#!/usr/bin/env python3
"""Update pdf_url for all peraturan by fetching from detail pages."""

import asyncio
import sqlite3
from pathlib import Path

import httpx
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

from src.services.parser import Parser


async def update_pdf_urls(db_path: str = "data/peraturan.db", batch_size: int = 50):
    """Update pdf_url for all peraturan."""

    parser = Parser()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all slugs that need pdf_url update
    cursor.execute("SELECT slug FROM peraturan WHERE pdf_url IS NULL OR pdf_url = ''")
    slugs = [row[0] for row in cursor.fetchall()]

    print(f"Total slugs to update: {len(slugs)}")

    updated = 0
    failed = 0
    no_pdf = 0

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TextColumn("Updated: {task.fields[updated]}"),
            TextColumn("No PDF: {task.fields[no_pdf]}"),
            TextColumn("Failed: {task.fields[failed]}"),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(
                "Updating PDF URLs",
                total=len(slugs),
                updated=0,
                no_pdf=0,
                failed=0,
            )

            for i in range(0, len(slugs), batch_size):
                batch = slugs[i:i + batch_size]

                for slug in batch:
                    url = f"https://peraturan.go.id/id/{slug}"

                    try:
                        resp = await client.get(url)

                        if resp.status_code == 200:
                            # Parse to extract PDF URL
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(resp.text, "lxml")
                            pdf_url = parser._extract_pdf_url(soup)

                            if pdf_url:
                                cursor.execute(
                                    "UPDATE peraturan SET pdf_url = ? WHERE slug = ?",
                                    (pdf_url, slug)
                                )
                                updated += 1
                            else:
                                no_pdf += 1
                        else:
                            failed += 1

                    except Exception as e:
                        failed += 1

                    progress.update(
                        task,
                        advance=1,
                        updated=updated,
                        no_pdf=no_pdf,
                        failed=failed,
                    )

                # Commit after each batch
                conn.commit()

                # Small delay between batches
                await asyncio.sleep(0.5)

    conn.close()

    print(f"\n=== 완료 ===")
    print(f"Updated: {updated}")
    print(f"No PDF found: {no_pdf}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    asyncio.run(update_pdf_urls())
