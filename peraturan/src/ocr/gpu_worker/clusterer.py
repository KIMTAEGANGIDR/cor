#!/usr/bin/env python3
"""
ILIS Header Clustering - GPU Server
헤더 텍스트 클러스터링 (TF-IDF + KMeans)

Usage:
    python3 clusterer.py run              # 클러스터링 실행
    python3 clusterer.py status           # 상태 확인
    python3 clusterer.py show --cluster 0 # 클러스터 샘플 보기
"""

import json
import sqlite3
from pathlib import Path

import click
import numpy as np
from rich.console import Console
from rich.table import Table
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

console = Console()

# Config
DB_PATH = Path("/mnt/workspace/images/ocr_pipeline.db")
N_CLUSTERS = 100  # 예상 패턴 수


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@click.group()
def cli():
    """ILIS Header Clustering"""
    pass


@cli.command()
@click.option('--clusters', '-c', type=int, default=N_CLUSTERS, help='Number of clusters')
def run(clusters):
    """Run TF-IDF + KMeans clustering"""
    if not DB_PATH.exists():
        console.print(f"[red]DB not found: {DB_PATH}[/red]")
        return

    conn = get_db()

    # OCR 완료된 헤더 조회
    console.print("[cyan]Loading header texts...[/cyan]")
    rows = conn.execute("""
        SELECT id, document_id, raw_text
        FROM headers
        WHERE raw_text IS NOT NULL AND raw_text != ''
    """).fetchall()

    if not rows:
        console.print("[red]No OCR results found![/red]")
        return

    console.print(f"[green]Found {len(rows)} headers with OCR text[/green]")

    # 데이터 준비
    header_ids = [row['id'] for row in rows]
    document_ids = [row['document_id'] for row in rows]
    texts = [row['raw_text'] for row in rows]

    # TF-IDF 벡터화
    console.print("[cyan]Vectorizing with TF-IDF...[/cyan]")
    vectorizer = TfidfVectorizer(
        max_features=1000,
        ngram_range=(1, 2),
        stop_words=None
    )
    X = vectorizer.fit_transform(texts)
    console.print(f"[green]TF-IDF matrix: {X.shape}[/green]")

    # 클러스터 수 결정
    n_clusters = min(clusters, int(np.sqrt(len(texts))), len(texts))
    console.print(f"[cyan]Clustering into {n_clusters} clusters...[/cyan]")

    # KMeans
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10, verbose=1)
    labels = kmeans.fit_predict(X)

    console.print("[cyan]Saving results to DB...[/cyan]")

    # 기존 클러스터 삭제
    conn.execute("DELETE FROM clusters")

    # 클러스터별 통계 수집
    cluster_stats = {}
    for i, (header_id, doc_id, label) in enumerate(zip(header_ids, document_ids, labels)):
        label = int(label)
        if label not in cluster_stats:
            cluster_stats[label] = {
                'count': 0,
                'samples': [],
                'sample_texts': []
            }
        cluster_stats[label]['count'] += 1
        if len(cluster_stats[label]['samples']) < 5:
            cluster_stats[label]['samples'].append(doc_id)
            cluster_stats[label]['sample_texts'].append(texts[i][:200])

        # 헤더에 클러스터 ID 저장
        conn.execute(
            "UPDATE headers SET cluster_id = ? WHERE id = ?",
            (label, header_id)
        )

    # 클러스터 테이블에 저장
    for cluster_id, stats in cluster_stats.items():
        # jenis 추출 (document_id에서)
        jenis_counts = {}
        for doc_id in stats['samples']:
            jenis = doc_id.split('-')[0].upper() if '-' in doc_id else 'OTHER'
            jenis_counts[jenis] = jenis_counts.get(jenis, 0) + 1
        main_jenis = max(jenis_counts, key=jenis_counts.get) if jenis_counts else 'UNKNOWN'

        conn.execute("""
            INSERT INTO clusters (id, name, description, document_count, sample_documents, representative_text, status)
            VALUES (?, ?, ?, ?, ?, ?, 'draft')
        """, (
            cluster_id,
            f"cluster_{cluster_id:03d}",
            f"Main: {main_jenis}",
            stats['count'],
            json.dumps(stats['samples']),
            stats['sample_texts'][0] if stats['sample_texts'] else ''
        ))

    conn.commit()
    conn.close()

    # 결과 출력
    console.print(f"\n[bold green]Clustering complete![/bold green]")
    console.print(f"Total documents: {len(texts)}")
    console.print(f"Clusters created: {n_clusters}")

    # 상위 10개 클러스터
    console.print("\n[bold]Top 10 clusters:[/bold]")
    sorted_clusters = sorted(cluster_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:10]

    table = Table()
    table.add_column("Cluster", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Sample")

    for cluster_id, stats in sorted_clusters:
        sample = stats['samples'][0] if stats['samples'] else ''
        table.add_row(
            f"#{cluster_id:03d}",
            str(stats['count']),
            sample[:40]
        )

    console.print(table)


@cli.command()
def status():
    """Show clustering status"""
    if not DB_PATH.exists():
        console.print(f"[red]DB not found: {DB_PATH}[/red]")
        return

    conn = get_db()

    # 헤더 상태
    header_stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN raw_text IS NOT NULL THEN 1 ELSE 0 END) as ocr_done,
            SUM(CASE WHEN cluster_id IS NOT NULL THEN 1 ELSE 0 END) as clustered
        FROM headers
    """).fetchone()

    console.print("\n[bold]Header Status:[/bold]")
    table = Table()
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Total headers", str(header_stats['total']))
    table.add_row("OCR completed", str(header_stats['ocr_done']))
    table.add_row("Clustered", str(header_stats['clustered']))
    console.print(table)

    # 클러스터 상태
    cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    console.print(f"\n[bold]Clusters:[/bold] {cluster_count}")

    if cluster_count > 0:
        rows = conn.execute("""
            SELECT id, name, document_count, description
            FROM clusters
            ORDER BY document_count DESC
            LIMIT 10
        """).fetchall()

        table = Table(title="Top 10 Clusters")
        table.add_column("ID")
        table.add_column("Name")
        table.add_column("Count", justify="right")
        table.add_column("Description")

        for row in rows:
            table.add_row(
                str(row['id']),
                row['name'],
                str(row['document_count']),
                row['description'] or ''
            )

        console.print(table)

    conn.close()


@cli.command()
@click.option('--cluster', '-c', type=int, required=True, help='Cluster ID to show')
def show(cluster):
    """Show cluster samples"""
    if not DB_PATH.exists():
        console.print(f"[red]DB not found: {DB_PATH}[/red]")
        return

    conn = get_db()

    # 클러스터 정보
    cluster_row = conn.execute(
        "SELECT * FROM clusters WHERE id = ?", (cluster,)
    ).fetchone()

    if not cluster_row:
        console.print(f"[red]Cluster {cluster} not found[/red]")
        return

    console.print(f"\n[bold]Cluster #{cluster}[/bold]")
    console.print(f"Name: {cluster_row['name']}")
    console.print(f"Documents: {cluster_row['document_count']}")
    console.print(f"Description: {cluster_row['description']}")

    # 샘플 헤더 텍스트
    rows = conn.execute("""
        SELECT document_id, raw_text, ocr_confidence
        FROM headers
        WHERE cluster_id = ?
        LIMIT 5
    """, (cluster,)).fetchall()

    console.print(f"\n[bold]Sample headers:[/bold]")
    for i, row in enumerate(rows, 1):
        console.print(f"\n[cyan]--- {i}. {row['document_id']} (conf: {row['ocr_confidence']:.2f}) ---[/cyan]")
        console.print(row['raw_text'][:500] if row['raw_text'] else '[No text]')

    conn.close()


if __name__ == '__main__':
    cli()
