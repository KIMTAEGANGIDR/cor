#!/usr/bin/env python3
"""Export reference extraction results to Obsidian markdown files."""

import json
import sqlite3
from pathlib import Path


def slug_to_obsidian_name(jenis: str, nomor: str, tahun: int) -> str:
    """Convert law reference to Obsidian-friendly filename."""
    jenis_short = {
        'UNDANG-UNDANG': 'UU',
        'UNDANG-UNDANG_DARURAT': 'UU-Darurat',
        'PERPPU': 'Perppu',
        'PERATURAN_PEMERINTAH': 'PP',
        'PERATURAN_PRESIDEN': 'Perpres',
        'PERATURAN_MENTERI': 'Permen',
        'KEPUTUSAN_PRESIDEN': 'Keppres',
        'PERATURAN_DAERAH': 'Perda',
        'STAATSBLAD': 'Stb',
        'LEMBARAN_NEGARA': 'LN',
    }.get(jenis, jenis)

    return f"{jenis_short} {nomor}-{tahun}"


def generate_obsidian_md(result: dict, db_path: str = "data/peraturan.db") -> str:
    """Generate Obsidian markdown content for a law document."""
    slug = result['slug']

    # Get metadata from DB
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT jenis, nomor, tahun, tentang, status, pemrakarsa
        FROM peraturan WHERE slug = ?
    """, (slug,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return ""

    jenis, nomor, tahun, tentang, status, pemrakarsa = row

    # Build markdown
    lines = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f"jenis: {jenis}")
    lines.append(f"nomor: {nomor}")
    lines.append(f"tahun: {tahun}")
    lines.append(f"pemrakarsa: {pemrakarsa or 'N/A'}")
    lines.append(f"status: {'Berlaku' if status and 'Berlaku' in status and 'Tidak' not in status else 'Tidak Berlaku' if status else 'Unknown'}")
    lines.append("tags:")
    lines.append(f"  - {jenis.lower().replace(' ', '-')}")
    lines.append(f"  - tahun-{tahun}")
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# {jenis} No. {nomor} Tahun {tahun}")
    lines.append("")
    lines.append(f"**Tentang:** {tentang or 'N/A'}")
    lines.append("")

    # External References
    if result['external_refs']:
        lines.append("## Referensi Eksternal")
        lines.append("")

        # Group by ref_type
        by_type = {}
        for ref in result['external_refs']:
            ref_type = ref['ref_type']
            if ref_type not in by_type:
                by_type[ref_type] = []
            by_type[ref_type].append(ref)

        type_icons = {
            'AMENDS': '🔄 Mengubah',
            'REVOKES': '❌ Mencabut',
            'IMPLEMENTS': '⚡ Melaksanakan',
            'REFERENCES': '📎 Merujuk',
        }

        for ref_type, refs in by_type.items():
            lines.append(f"### {type_icons.get(ref_type, ref_type)}")
            lines.append("")
            for ref in refs:
                link_name = slug_to_obsidian_name(ref['jenis'], ref['nomor'], ref['tahun'])
                tentang_str = f" - {ref['tentang'][:50]}..." if ref.get('tentang') and len(ref['tentang']) > 50 else (f" - {ref['tentang']}" if ref.get('tentang') else "")
                lines.append(f"- [[{link_name}]]{tentang_str}")
            lines.append("")

    # Internal References
    if result['internal_refs']:
        lines.append("## Referensi Internal")
        lines.append("")
        # Show unique targets only
        seen = set()
        for ref in result['internal_refs']:
            target = ref['target_value']
            if target not in seen:
                seen.add(target)
                lines.append(f"- {target}")
        lines.append("")

    # Conditional Clauses
    if result['conditional_clauses']:
        lines.append("## Klausul Bersyarat")
        lines.append("")

        clause_icons = {
            'SEPANJANG_TIDAK_BERTENTANGAN': '⚠️ Berlaku sepanjang tidak bertentangan',
            'TETAP_BERLAKU_SAMPAI': '⏳ Tetap berlaku sampai',
            'DICABUT_DAN_TIDAK_BERLAKU': '🚫 Dicabut dan tidak berlaku',
        }

        for clause in result['conditional_clauses']:
            clause_type = clause['clause_type']
            lines.append(f"### {clause_icons.get(clause_type, clause_type)}")
            lines.append("")
            lines.append(f"> {clause['text']}")
            lines.append("")

            if clause.get('target_law'):
                target = clause['target_law']
                link_name = slug_to_obsidian_name(target['jenis'], target['nomor'], target['tahun'])
                lines.append(f"**Target:** [[{link_name}]]")
                lines.append("")

    # Statistics
    lines.append("## Statistik")
    lines.append("")
    lines.append(f"| Jenis | Jumlah |")
    lines.append(f"|-------|--------|")
    lines.append(f"| Referensi Eksternal | {len(result['external_refs'])} |")
    lines.append(f"| Referensi Internal | {len(result['internal_refs'])} |")
    lines.append(f"| Klausul Bersyarat | {len(result['conditional_clauses'])} |")
    lines.append("")

    return "\n".join(lines)


def export_to_obsidian(json_path: str = "data/reference_extraction_test.json",
                       output_dir: str = "data/obsidian_sample"):
    """Export all results to Obsidian markdown files."""

    # Load results
    with open(json_path, 'r', encoding='utf-8') as f:
        results = json.load(f)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("Obsidian Markdown Export")
    print(f"{'='*60}")

    # Get slug to name mapping from DB
    conn = sqlite3.connect("data/peraturan.db")
    cursor = conn.cursor()

    for result in results:
        slug = result['slug']

        # Get metadata
        cursor.execute("SELECT jenis, nomor, tahun FROM peraturan WHERE slug = ?", (slug,))
        row = cursor.fetchone()
        if not row:
            continue

        jenis, nomor, tahun = row

        # Generate filename
        jenis_short = {
            'UNDANG-UNDANG': 'UU',
            'PERATURAN PEMERINTAH': 'PP',
            'PERATURAN MENTERI': 'Permen',
        }.get(jenis, jenis.split()[0] if jenis else 'Unknown')

        filename = f"{jenis_short} {nomor}-{tahun}.md"
        filepath = output_path / filename

        # Generate content
        content = generate_obsidian_md(result)

        # Write file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"✅ {filename}")
        print(f"   외부: {len(result['external_refs'])}건 | 내부: {len(result['internal_refs'])}건 | 조건부: {len(result['conditional_clauses'])}건")

    conn.close()

    # Create index file
    index_content = generate_index(results)
    with open(output_path / "00_INDEX.md", 'w', encoding='utf-8') as f:
        f.write(index_content)
    print(f"\n✅ 00_INDEX.md")

    print(f"\n{'='*60}")
    print(f"Export complete: {output_path}")
    print(f"Open this folder as an Obsidian vault to view the graph!")
    print(f"{'='*60}")


def generate_index(results: list) -> str:
    """Generate index markdown file."""
    lines = []
    lines.append("# Law Reference Extraction - Sample Index")
    lines.append("")
    lines.append("## Documents")
    lines.append("")

    conn = sqlite3.connect("data/peraturan.db")
    cursor = conn.cursor()

    for result in results:
        slug = result['slug']
        cursor.execute("SELECT jenis, nomor, tahun, tentang FROM peraturan WHERE slug = ?", (slug,))
        row = cursor.fetchone()
        if row:
            jenis, nomor, tahun, tentang = row
            jenis_short = {
                'UNDANG-UNDANG': 'UU',
                'PERATURAN PEMERINTAH': 'PP',
                'PERATURAN MENTERI': 'Permen',
            }.get(jenis, jenis.split()[0] if jenis else 'Unknown')

            link_name = f"{jenis_short} {nomor}-{tahun}"
            lines.append(f"- [[{link_name}]] - {tentang[:60] if tentang else 'N/A'}...")

    conn.close()

    lines.append("")
    lines.append("## Statistics")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Documents | {len(results)} |")
    lines.append(f"| Total External Refs | {sum(len(r['external_refs']) for r in results)} |")
    lines.append(f"| Total Internal Refs | {sum(len(r['internal_refs']) for r in results)} |")
    lines.append(f"| Total Conditional Clauses | {sum(len(r['conditional_clauses']) for r in results)} |")
    lines.append("")

    lines.append("## Graph View")
    lines.append("")
    lines.append("Open Obsidian's **Graph View** (Ctrl/Cmd + G) to visualize the relationships!")
    lines.append("")
    lines.append("### Legend")
    lines.append("- 🔄 AMENDS - Mengubah (개정)")
    lines.append("- ❌ REVOKES - Mencabut (폐지)")
    lines.append("- ⚡ IMPLEMENTS - Melaksanakan (시행)")
    lines.append("- 📎 REFERENCES - Merujuk (참조)")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    export_to_obsidian()
