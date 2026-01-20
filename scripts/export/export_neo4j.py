#!/usr/bin/env python3
"""Export reference data to Neo4j Cypher format."""

import json
import sqlite3
from pathlib import Path


def export_to_neo4j(json_path: str = "data/all_references.json",
                    db_path: str = "data/peraturan.db",
                    output_dir: str = "data/neo4j_export"):
    """Export to Neo4j Cypher import files."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Load extraction results
    with open(json_path, 'r', encoding='utf-8') as f:
        results = json.load(f)

    print(f"\n{'='*60}")
    print("Neo4j Export")
    print(f"{'='*60}")
    print(f"문서 수: {len(results):,}건")

    # Collect all nodes and relationships
    nodes = {}
    relationships = []

    # Type mapping
    type_map = {
        'UNDANG-UNDANG': 'UU',
        'PERATURAN_PEMERINTAH': 'PP',
        'PERATURAN PEMERINTAH': 'PP',
        'PERATURAN_PRESIDEN': 'Perpres',
        'PERATURAN_MENTERI': 'Permen',
        'PERATURAN MENTERI': 'Permen',
        'PERPPU': 'Perppu',
        'UNDANG-UNDANG_DARURAT': 'UU_Darurat',
        'STAATSBLAD': 'Staatsblad',
        'KEPUTUSAN_PRESIDEN': 'Keppres',
        'KEPUTUSAN_MENTERI': 'Kepmen',
        'LEMBARAN_NEGARA': 'LN',
    }

    for result in results:
        slug = result['slug']
        jenis = result.get('jenis', 'UNKNOWN')
        nomor = result.get('nomor', '?')
        tahun = result.get('tahun', 0)
        tentang = result.get('tentang', '') or ''

        # Source node
        jenis_short = type_map.get(jenis, jenis.split()[0] if jenis else 'Unknown')
        source_id = f"{jenis_short}_{nomor}_{tahun}".replace('/', '_').replace(' ', '_')

        if source_id not in nodes:
            nodes[source_id] = {
                'id': source_id,
                'slug': slug,
                'jenis': jenis,
                'jenis_short': jenis_short,
                'nomor': nomor,
                'tahun': tahun,
                'tentang': tentang[:200].replace('"', "'").replace('\\', ''),
                'label': f"{jenis_short} {nomor}/{tahun}",
                'has_conditional': len(result.get('conditional_clauses', [])) > 0,
                'is_source': True
            }

        # Process external references
        for ref in result.get('external_refs', []):
            ref_jenis = ref['jenis']
            ref_jenis_short = type_map.get(ref_jenis, ref_jenis.split('_')[0] if ref_jenis else 'Unknown')
            target_id = f"{ref_jenis_short}_{ref['nomor']}_{ref['tahun']}".replace('/', '_').replace(' ', '_')

            if target_id not in nodes:
                ref_tentang = ref.get('tentang', '') or ''
                nodes[target_id] = {
                    'id': target_id,
                    'slug': None,
                    'jenis': ref_jenis,
                    'jenis_short': ref_jenis_short,
                    'nomor': ref['nomor'],
                    'tahun': ref['tahun'],
                    'tentang': ref_tentang[:200].replace('"', "'").replace('\\', ''),
                    'label': f"{ref_jenis_short} {ref['nomor']}/{ref['tahun']}",
                    'has_conditional': False,
                    'is_source': False
                }

            relationships.append({
                'source': source_id,
                'target': target_id,
                'type': ref['ref_type'],
                'context': (ref.get('context', '') or '')[:100].replace('"', "'").replace('\\', '')
            })

        # Process conditional clauses
        for clause in result.get('conditional_clauses', []):
            if clause.get('target_law'):
                target = clause['target_law']
                ref_jenis_short = type_map.get(target['jenis'], target['jenis'].split('_')[0])
                target_id = f"{ref_jenis_short}_{target['nomor']}_{target['tahun']}".replace('/', '_').replace(' ', '_')

                relationships.append({
                    'source': source_id,
                    'target': target_id,
                    'type': 'CONDITIONAL_ON',
                    'context': clause['clause_type']
                })

    print(f"노드 수: {len(nodes):,}개")
    print(f"관계 수: {len(relationships):,}개")

    # Generate Cypher files
    # 1. Create constraints and indexes
    with open(output_path / "01_schema.cypher", 'w', encoding='utf-8') as f:
        f.write("""// Neo4j Schema - Run this first
// ================================

// Constraints
CREATE CONSTRAINT peraturan_id IF NOT EXISTS FOR (p:Peraturan) REQUIRE p.id IS UNIQUE;

// Indexes for faster queries
CREATE INDEX peraturan_jenis IF NOT EXISTS FOR (p:Peraturan) ON (p.jenis);
CREATE INDEX peraturan_tahun IF NOT EXISTS FOR (p:Peraturan) ON (p.tahun);
CREATE INDEX peraturan_slug IF NOT EXISTS FOR (p:Peraturan) ON (p.slug);

// Full-text search index
CREATE FULLTEXT INDEX peraturan_search IF NOT EXISTS FOR (p:Peraturan) ON EACH [p.tentang, p.label];
""")

    # 2. Create nodes in batches
    batch_size = 1000
    node_list = list(nodes.values())

    with open(output_path / "02_nodes.cypher", 'w', encoding='utf-8') as f:
        f.write("// Neo4j Nodes - Peraturan\n")
        f.write(f"// Total: {len(node_list):,} nodes\n")
        f.write("// ================================\n\n")

        for i in range(0, len(node_list), batch_size):
            batch = node_list[i:i+batch_size]
            f.write(f"// Batch {i//batch_size + 1}\n")

            for node in batch:
                slug_str = f'"{node["slug"]}"' if node["slug"] else 'null'
                f.write(f'''MERGE (p:Peraturan:{node["jenis_short"]} {{id: "{node["id"]}"}})
SET p.slug = {slug_str},
    p.jenis = "{node["jenis"]}",
    p.nomor = "{node["nomor"]}",
    p.tahun = {node["tahun"]},
    p.tentang = "{node["tentang"]}",
    p.label = "{node["label"]}",
    p.has_conditional = {str(node["has_conditional"]).lower()},
    p.is_source = {str(node["is_source"]).lower()};
''')
            f.write("\n")

    # 3. Create relationships in batches
    with open(output_path / "03_relationships.cypher", 'w', encoding='utf-8') as f:
        f.write("// Neo4j Relationships\n")
        f.write(f"// Total: {len(relationships):,} relationships\n")
        f.write("// ================================\n\n")

        # Group by relationship type
        by_type = {}
        for rel in relationships:
            rel_type = rel['type']
            if rel_type not in by_type:
                by_type[rel_type] = []
            by_type[rel_type].append(rel)

        for rel_type, rels in by_type.items():
            f.write(f"// {rel_type}: {len(rels):,} relationships\n")

            for i in range(0, len(rels), batch_size):
                batch = rels[i:i+batch_size]

                for rel in batch:
                    f.write(f'''MATCH (a:Peraturan {{id: "{rel["source"]}"}}), (b:Peraturan {{id: "{rel["target"]}"}})
MERGE (a)-[r:{rel_type}]->(b)
SET r.context = "{rel["context"]}";
''')
                f.write("\n")

    # 4. Create useful queries file
    with open(output_path / "04_sample_queries.cypher", 'w', encoding='utf-8') as f:
        f.write("""// Sample Neo4j Queries
// ================================

// 1. Find all laws that a specific law references
MATCH (p:Peraturan {label: "UU 6/2023"})-[r]->(target)
RETURN p.label, type(r), target.label, target.tentang
LIMIT 50;

// 2. Find all laws that reference a specific law (incoming)
MATCH (source)-[r]->(p:Peraturan {label: "UU 11/2020"})
RETURN source.label, type(r), p.label
LIMIT 50;

// 3. Find amendment chains
MATCH path = (p:Peraturan)-[:AMENDS*1..5]->(original)
WHERE p.jenis = "UNDANG-UNDANG"
RETURN path
LIMIT 20;

// 4. Find laws with conditional validity
MATCH (p:Peraturan {has_conditional: true})-[r:CONDITIONAL_ON]->(target)
RETURN p.label, p.tentang, r.context, target.label
LIMIT 50;

// 5. Find most referenced laws (hub nodes)
MATCH (p:Peraturan)<-[r]-(source)
WITH p, count(r) as incoming
ORDER BY incoming DESC
RETURN p.label, p.tentang, incoming
LIMIT 20;

// 6. Find laws by year with relationships
MATCH (p:Peraturan {tahun: 2023})-[r]->(target)
RETURN p.label, type(r), target.label
LIMIT 100;

// 7. Find revocation chains
MATCH path = (newer)-[:REVOKES]->(older)
RETURN newer.label, older.label, older.tentang
LIMIT 30;

// 8. Find implementation hierarchy (PP implementing UU)
MATCH (pp:PP)-[:IMPLEMENTS]->(uu:UU)
RETURN pp.label, uu.label, uu.tentang
LIMIT 30;

// 9. Full-text search
CALL db.index.fulltext.queryNodes("peraturan_search", "Cipta Kerja")
YIELD node, score
RETURN node.label, node.tentang, score
LIMIT 10;

// 10. Graph statistics
MATCH (p:Peraturan)
RETURN p.jenis_short as type, count(*) as count
ORDER BY count DESC;
""")

    # 5. Create CSV files for neo4j-admin import (faster for large datasets)
    # Nodes CSV
    with open(output_path / "nodes.csv", 'w', encoding='utf-8') as f:
        f.write("id:ID,slug,jenis,jenis_short,nomor,tahun:int,tentang,label,has_conditional:boolean,is_source:boolean,:LABEL\n")
        for node in node_list:
            slug = node['slug'] or ''
            tentang = node['tentang'].replace('"', '""')
            labels = f"Peraturan;{node['jenis_short']}"
            f.write(f'"{node["id"]}","{slug}","{node["jenis"]}","{node["jenis_short"]}","{node["nomor"]}",{node["tahun"]},"{tentang}","{node["label"]}",{str(node["has_conditional"]).lower()},{str(node["is_source"]).lower()},{labels}\n')

    # Relationships CSV
    with open(output_path / "relationships.csv", 'w', encoding='utf-8') as f:
        f.write(":START_ID,:END_ID,:TYPE,context\n")
        for rel in relationships:
            context = rel['context'].replace('"', '""')
            f.write(f'"{rel["source"]}","{rel["target"]}",{rel["type"]},"{context}"\n')

    print(f"\n{'='*60}")
    print("Export 완료!")
    print(f"{'='*60}")
    print(f"\n출력 디렉토리: {output_path}")
    print(f"\n파일 목록:")
    print(f"  - 01_schema.cypher      (스키마 생성)")
    print(f"  - 02_nodes.cypher       (노드 {len(nodes):,}개)")
    print(f"  - 03_relationships.cypher (관계 {len(relationships):,}개)")
    print(f"  - 04_sample_queries.cypher (샘플 쿼리)")
    print(f"  - nodes.csv             (CSV import용)")
    print(f"  - relationships.csv     (CSV import용)")

    print(f"\n{'='*60}")
    print("Neo4j Import 방법")
    print(f"{'='*60}")
    print("""
[방법 1: Cypher 직접 실행]
1. Neo4j Desktop 또는 Browser 열기
2. 01_schema.cypher 실행
3. 02_nodes.cypher 실행
4. 03_relationships.cypher 실행

[방법 2: CSV Import (대용량 권장)]
neo4j-admin database import full \\
  --nodes=import/nodes.csv \\
  --relationships=import/relationships.csv \\
  neo4j

[방법 3: LOAD CSV]
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
CREATE (p:Peraturan {id: row.id, ...})
""")

    return output_path


if __name__ == "__main__":
    export_to_neo4j()
