#!/usr/bin/env python3
"""Extract all references and create D3.js visualization."""

import json
import sqlite3
import time
from pathlib import Path
from extract_references import ReferenceExtractor

def extract_all_references(db_path: str = "data/peraturan.db",
                           output_path: str = "data/all_references.json",
                           limit: int = None):
    """Extract references from all documents."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all documents with text
    query = """
        SELECT slug, jenis, nomor, tahun, tentang, extracted_text
        FROM peraturan
        WHERE extracted_text IS NOT NULL AND length(extracted_text) > 100
    """
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    rows = cursor.fetchall()
    total = len(rows)

    print(f"\n{'='*60}")
    print(f"전체 법령 참조 추출")
    print(f"{'='*60}")
    print(f"대상 문서: {total:,}건")
    print(f"{'='*60}\n")

    extractor = ReferenceExtractor()
    results = []

    start_time = time.time()

    for i, (slug, jenis, nomor, tahun, tentang, text) in enumerate(rows):
        # Progress
        if (i + 1) % 1000 == 0 or i == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total - i - 1) / rate if rate > 0 else 0
            print(f"[{i+1:,}/{total:,}] {slug} | {rate:.1f} docs/sec | ETA: {eta/60:.1f}min")

        # Reset stats
        extractor.stats = {k: 0 for k in extractor.stats}

        # Extract
        result = extractor.extract_all(text, slug)

        # Add metadata
        result['jenis'] = jenis
        result['nomor'] = nomor
        result['tahun'] = tahun
        result['tentang'] = tentang

        # Only keep if has any references
        if result['external_refs'] or result['conditional_clauses']:
            results.append(result)

    conn.close()

    elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"추출 완료")
    print(f"{'='*60}")
    print(f"처리 시간: {elapsed/60:.1f}분")
    print(f"관계 있는 문서: {len(results):,}건 / {total:,}건")
    print(f"총 외부 참조: {sum(len(r['external_refs']) for r in results):,}건")
    print(f"총 조건부 조항: {sum(len(r['conditional_clauses']) for r in results):,}건")

    # Save results
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)
    print(f"\n결과 저장: {output_path}")

    return results


def create_d3_visualization(results: list, output_path: str = "data/graph_full.html", max_nodes: int = 500):
    """Create D3.js visualization from results."""

    print(f"\n{'='*60}")
    print(f"D3.js 시각화 생성")
    print(f"{'='*60}")

    # Build nodes and links
    nodes = {}
    links = []

    # Node type colors
    type_map = {
        'UNDANG-UNDANG': 'UU',
        'PERATURAN_PEMERINTAH': 'PP',
        'PERATURAN PEMERINTAH': 'PP',
        'PERATURAN_PRESIDEN': 'Perpres',
        'PERATURAN_MENTERI': 'Permen',
        'PERATURAN MENTERI': 'Permen',
        'PERPPU': 'Perppu',
        'UNDANG-UNDANG_DARURAT': 'UU-Darurat',
        'STAATSBLAD': 'Stb',
        'KEPUTUSAN_PRESIDEN': 'Keppres',
    }

    # Process results
    for result in results:
        slug = result['slug']
        jenis = result.get('jenis', 'UNKNOWN')
        nomor = result.get('nomor', '?')
        tahun = result.get('tahun', 0)
        tentang = result.get('tentang', '')[:100]

        # Source node
        jenis_short = type_map.get(jenis, jenis.split()[0] if jenis else 'Unknown')
        source_id = f"{jenis_short} {nomor}-{tahun}"

        if source_id not in nodes:
            nodes[source_id] = {
                'id': source_id,
                'type': jenis,
                'label': source_id,
                'tentang': tentang,
                'main': True,
                'refs_count': len(result['external_refs'])
            }

        # Target nodes from references
        for ref in result['external_refs']:
            ref_jenis = ref['jenis']
            ref_jenis_short = type_map.get(ref_jenis, ref_jenis.split('_')[0] if ref_jenis else 'Unknown')
            target_id = f"{ref_jenis_short} {ref['nomor']}-{ref['tahun']}"

            if target_id not in nodes:
                nodes[target_id] = {
                    'id': target_id,
                    'type': ref_jenis,
                    'label': target_id,
                    'tentang': ref.get('tentang', '')[:100] if ref.get('tentang') else '',
                    'main': False,
                    'refs_count': 0
                }

            links.append({
                'source': source_id,
                'target': target_id,
                'type': ref['ref_type']
            })

    print(f"전체 노드: {len(nodes):,}개")
    print(f"전체 링크: {len(links):,}개")

    # Filter to top nodes by reference count if too many
    if len(nodes) > max_nodes:
        print(f"\n노드 수 제한: {max_nodes}개로 필터링")

        # Sort by refs_count and connection count
        node_connections = {}
        for link in links:
            node_connections[link['source']] = node_connections.get(link['source'], 0) + 1
            node_connections[link['target']] = node_connections.get(link['target'], 0) + 1

        # Keep top connected nodes
        sorted_nodes = sorted(nodes.keys(), key=lambda x: node_connections.get(x, 0), reverse=True)
        keep_nodes = set(sorted_nodes[:max_nodes])

        # Filter
        nodes = {k: v for k, v in nodes.items() if k in keep_nodes}
        links = [l for l in links if l['source'] in keep_nodes and l['target'] in keep_nodes]

        print(f"필터링 후 노드: {len(nodes):,}개")
        print(f"필터링 후 링크: {len(links):,}개")

    # Generate HTML
    html_content = generate_d3_html(list(nodes.values()), links)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"\n시각화 저장: {output_path}")
    return output_path


def generate_d3_html(nodes: list, links: list) -> str:
    """Generate D3.js HTML content."""

    nodes_json = json.dumps(nodes, ensure_ascii=False)
    links_json = json.dumps(links, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Indonesian Law Reference Graph - Full</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            overflow: hidden;
        }}
        #graph {{ width: 100vw; height: 100vh; }}
        .node {{ cursor: pointer; }}
        .node circle {{ stroke: #fff; stroke-width: 1.5px; }}
        .node text {{ font-size: 8px; fill: #fff; pointer-events: none; text-anchor: middle; }}
        .link {{ fill: none; stroke-opacity: 0.4; }}
        .link-label {{ font-size: 7px; fill: #666; pointer-events: none; }}
        .legend {{
            position: fixed; top: 20px; right: 20px;
            background: rgba(0,0,0,0.85); padding: 15px;
            border-radius: 8px; font-size: 11px;
        }}
        .legend h3 {{ margin-bottom: 10px; color: #4a9eff; font-size: 13px; }}
        .legend-item {{ display: flex; align-items: center; margin: 4px 0; }}
        .legend-color {{ width: 14px; height: 14px; border-radius: 50%; margin-right: 8px; }}
        .legend-line {{ width: 25px; height: 3px; margin-right: 8px; }}
        .title {{ position: fixed; top: 20px; left: 20px; font-size: 20px; font-weight: bold; color: #4a9eff; }}
        .stats {{ position: fixed; top: 50px; left: 20px; font-size: 12px; color: #888; }}
        .tooltip {{
            position: absolute; background: rgba(0,0,0,0.95);
            padding: 10px 15px; border-radius: 5px; font-size: 11px;
            pointer-events: none; opacity: 0; transition: opacity 0.2s;
            max-width: 300px; border: 1px solid #333;
        }}
        .tooltip h4 {{ color: #4a9eff; margin-bottom: 5px; }}
        .controls {{
            position: fixed; bottom: 20px; left: 20px;
            background: rgba(0,0,0,0.85); padding: 10px 15px;
            border-radius: 8px; font-size: 11px;
        }}
        .controls button {{
            background: #4a9eff; color: #fff; border: none;
            padding: 5px 10px; margin: 2px; border-radius: 4px; cursor: pointer;
        }}
        .controls button:hover {{ background: #3a8eef; }}
        .search {{
            position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
            background: rgba(0,0,0,0.85); padding: 10px;
            border-radius: 8px;
        }}
        .search input {{
            background: #333; border: 1px solid #555; color: #fff;
            padding: 8px 15px; border-radius: 4px; width: 300px;
        }}
    </style>
</head>
<body>
    <div class="title">Indonesian Law Reference Graph</div>
    <div class="stats">Nodes: {len(nodes):,} | Links: {len(links):,}</div>

    <div class="search">
        <input type="text" id="searchInput" placeholder="Search law (e.g., UU 6-2023)...">
    </div>

    <div id="graph"></div>

    <div class="legend">
        <h3>Node Types</h3>
        <div class="legend-item"><div class="legend-color" style="background: #4a9eff;"></div><span>UU</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #67c23a;"></div><span>PP</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #e6a23c;"></div><span>Perppu/Darurat</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #909399;"></div><span>Staatsblad</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #f56c6c;"></div><span>Permen</span></div>
        <div class="legend-item"><div class="legend-color" style="background: #9b59b6;"></div><span>Perpres</span></div>
        <h3 style="margin-top: 12px;">Relationships</h3>
        <div class="legend-item"><div class="legend-line" style="background: #4a9eff;"></div><span>REFERENCES</span></div>
        <div class="legend-item"><div class="legend-line" style="background: #e6a23c;"></div><span>AMENDS</span></div>
        <div class="legend-item"><div class="legend-line" style="background: #f56c6c;"></div><span>REVOKES</span></div>
        <div class="legend-item"><div class="legend-line" style="background: #67c23a;"></div><span>IMPLEMENTS</span></div>
    </div>

    <div class="controls">
        <button onclick="zoomIn()">+ Zoom</button>
        <button onclick="zoomOut()">- Zoom</button>
        <button onclick="resetZoom()">Reset</button>
    </div>

    <div class="tooltip" id="tooltip"></div>

    <script>
        const data = {{
            nodes: {nodes_json},
            links: {links_json}
        }};

        const nodeColors = {{
            "UNDANG-UNDANG": "#4a9eff",
            "PERATURAN_PEMERINTAH": "#67c23a",
            "PERATURAN PEMERINTAH": "#67c23a",
            "PERATURAN_MENTERI": "#f56c6c",
            "PERATURAN MENTERI": "#f56c6c",
            "PERPPU": "#e6a23c",
            "UNDANG-UNDANG_DARURAT": "#e6a23c",
            "STAATSBLAD": "#909399",
            "PERATURAN_PRESIDEN": "#9b59b6",
            "KEPUTUSAN_PRESIDEN": "#9b59b6"
        }};

        const linkColors = {{
            "REFERENCES": "#4a9eff",
            "AMENDS": "#e6a23c",
            "REVOKES": "#f56c6c",
            "IMPLEMENTS": "#67c23a"
        }};

        const width = window.innerWidth;
        const height = window.innerHeight;

        const svg = d3.select("#graph")
            .append("svg")
            .attr("width", width)
            .attr("height", height);

        const g = svg.append("g");

        // Zoom
        const zoom = d3.zoom()
            .scaleExtent([0.05, 8])
            .on("zoom", (event) => g.attr("transform", event.transform));
        svg.call(zoom);

        window.zoomIn = () => svg.transition().call(zoom.scaleBy, 1.5);
        window.zoomOut = () => svg.transition().call(zoom.scaleBy, 0.67);
        window.resetZoom = () => svg.transition().call(zoom.transform, d3.zoomIdentity);

        // Arrow markers
        const defs = svg.append("defs");
        Object.keys(linkColors).forEach(type => {{
            defs.append("marker")
                .attr("id", `arrow-${{type}}`)
                .attr("viewBox", "0 -5 10 10")
                .attr("refX", 20)
                .attr("refY", 0)
                .attr("markerWidth", 4)
                .attr("markerHeight", 4)
                .attr("orient", "auto")
                .append("path")
                .attr("fill", linkColors[type])
                .attr("d", "M0,-5L10,0L0,5");
        }});

        // Simulation
        const simulation = d3.forceSimulation(data.nodes)
            .force("link", d3.forceLink(data.links).id(d => d.id).distance(80))
            .force("charge", d3.forceManyBody().strength(-150))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide().radius(25));

        // Links
        const link = g.append("g")
            .selectAll("line")
            .data(data.links)
            .enter()
            .append("line")
            .attr("class", "link")
            .attr("stroke", d => linkColors[d.type] || "#666")
            .attr("stroke-width", 1)
            .attr("marker-end", d => `url(#arrow-${{d.type}})`);

        // Nodes
        const node = g.append("g")
            .selectAll("g")
            .data(data.nodes)
            .enter()
            .append("g")
            .attr("class", "node")
            .call(d3.drag()
                .on("start", dragstarted)
                .on("drag", dragged)
                .on("end", dragended));

        node.append("circle")
            .attr("r", d => d.main ? Math.min(8 + d.refs_count * 0.5, 25) : 6)
            .attr("fill", d => nodeColors[d.type] || "#666")
            .attr("opacity", d => d.main ? 0.9 : 0.7);

        node.append("text")
            .attr("dy", -12)
            .attr("opacity", 0.8)
            .text(d => d.label.length > 15 ? d.label.substring(0, 15) + "..." : d.label);

        // Tooltip
        const tooltip = d3.select("#tooltip");
        node.on("mouseover", function(event, d) {{
            tooltip.style("opacity", 1)
                .html(`<h4>${{d.label}}</h4><p>${{d.tentang || 'N/A'}}</p><p><small>Type: ${{d.type}}</small></p>`)
                .style("left", (event.pageX + 10) + "px")
                .style("top", (event.pageY - 10) + "px");
            d3.select(this).select("circle").attr("stroke-width", 3);
        }})
        .on("mouseout", function() {{
            tooltip.style("opacity", 0);
            d3.select(this).select("circle").attr("stroke-width", 1.5);
        }});

        // Tick
        simulation.on("tick", () => {{
            link
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);
            node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
        }});

        // Drag
        function dragstarted(event, d) {{
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
        }}
        function dragged(event, d) {{ d.fx = event.x; d.fy = event.y; }}
        function dragended(event, d) {{
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
        }}

        // Search
        document.getElementById("searchInput").addEventListener("input", function(e) {{
            const query = e.target.value.toLowerCase();
            node.select("circle")
                .attr("stroke", d => d.label.toLowerCase().includes(query) && query ? "#fff" : "none")
                .attr("stroke-width", d => d.label.toLowerCase().includes(query) && query ? 4 : 1.5);
        }});
    </script>
</body>
</html>'''


if __name__ == "__main__":
    # Extract all
    results = extract_all_references()

    # Create visualization
    create_d3_visualization(results, max_nodes=800)

    print(f"\n완료! 브라우저에서 data/graph_full.html 을 열어보세요.")
