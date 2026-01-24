#!/usr/bin/env python3
"""
Surya Agent Worklog - 헤더 OCR + 클러스터링 작업 전용 워크로그
1분마다 자동 실행되어 작업 상태를 기록
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

# 경로 설정
DB_PATH = Path("/home/tylor/cor/peraturan/data/db/ocr_pipeline.db")
WORKLOG_PATH = Path("/home/tylor/cor/docs/surya_agent_worklog.md")
STATE_FILE = Path("/home/tylor/cor/.surya_agent_state.json")

JENIS_MAP = {
    "UNDANG-UNDANG": "UU",
    "PERATURAN PEMERINTAH": "PP",
    "PERATURAN PRESIDEN": "PERPRES",
    "PERATURAN MENTERI": "PERMEN",
    "PERATURAN BADAN/LEMBAGA": "PERBAN",
    "PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG": "PERPPU",
    "PERATURAN DAERAH": "PERDA",
    "KEPUTUSAN PRESIDEN": "KEPPRES",
}


def load_state():
    """이전 상태 로드"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {"surya_completed": 0, "clustered": 0, "last_update": None}


def save_state(state):
    """상태 저장"""
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def get_current_stats():
    """현재 DB 통계 조회"""
    if not DB_PATH.exists():
        return None

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row

    stats = {}

    # 전체 헤더 수
    stats["total"] = conn.execute("SELECT COUNT(*) FROM headers").fetchone()[0]

    # Surya OCR 완료
    try:
        stats["surya_completed"] = conn.execute(
            "SELECT COUNT(*) FROM headers WHERE surya_text IS NOT NULL"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        stats["surya_completed"] = 0

    # 클러스터링 완료
    try:
        stats["clustered"] = conn.execute(
            "SELECT COUNT(*) FROM headers WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
    except:
        stats["clustered"] = 0

    # 클러스터 수
    try:
        stats["cluster_count"] = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    except:
        stats["cluster_count"] = 0

    # 법령 유형별 Surya 진행률
    stats["by_jenis"] = {}
    try:
        rows = conn.execute("""
            SELECT d.jenis,
                   COUNT(*) as total,
                   SUM(CASE WHEN h.surya_text IS NOT NULL THEN 1 ELSE 0 END) as done
            FROM documents d
            JOIN headers h ON d.id = h.document_id
            GROUP BY d.jenis
            ORDER BY total DESC
        """).fetchall()

        for row in rows:
            jenis = row["jenis"]
            short = JENIS_MAP.get(jenis, jenis[:10])
            stats["by_jenis"][short] = {
                "total": row["total"],
                "done": row["done"],
                "pct": (row["done"] / row["total"] * 100) if row["total"] > 0 else 0
            }
    except:
        pass

    conn.close()
    return stats


def update_worklog():
    """워크로그 업데이트"""
    prev_state = load_state()
    current = get_current_stats()

    if not current:
        print(f"[{datetime.now():%H:%M:%S}] DB 없음")
        return

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 변화 계산
    surya_diff = current["surya_completed"] - prev_state.get("surya_completed", 0)
    cluster_diff = current["clustered"] - prev_state.get("clustered", 0)

    # 워크로그 파일 읽기/생성
    if WORKLOG_PATH.exists():
        content = WORKLOG_PATH.read_text()
    else:
        content = """# Surya Agent Worklog

헤더 OCR + 클러스터링 작업 자동 기록 (1분마다 갱신)

---

"""

    # 오늘 날짜 섹션
    today = now.strftime("%Y-%m-%d")
    date_header = f"## {today}"

    # 상태 요약
    surya_pct = (current["surya_completed"] / current["total"] * 100) if current["total"] > 0 else 0

    # 새 로그 엔트리
    if surya_diff > 0 or cluster_diff > 0:
        change_str = []
        if surya_diff > 0:
            change_str.append(f"+{surya_diff} OCR")
        if cluster_diff > 0:
            change_str.append(f"+{cluster_diff} 클러스터")
        change_text = ", ".join(change_str)
    else:
        change_text = "변화 없음"

    # 유형별 진행률 (간략)
    jenis_summary = []
    for jenis, data in current["by_jenis"].items():
        if data["pct"] > 0 and data["pct"] < 100:
            jenis_summary.append(f"{jenis}:{data['done']}/{data['total']}")
        elif data["pct"] >= 100:
            jenis_summary.append(f"{jenis}:완료")

    jenis_text = " | ".join(jenis_summary[:4]) if jenis_summary else ""

    entry = f"- **{now.strftime('%H:%M')}** OCR {current['surya_completed']:,}/{current['total']:,} ({surya_pct:.1f}%) [{change_text}]"
    if jenis_text:
        entry += f"\n  - {jenis_text}"

    # 날짜 섹션 찾기/추가
    if date_header in content:
        # 기존 날짜 섹션에 추가
        idx = content.find(date_header)
        section_end = content.find("\n## ", idx + 1)
        if section_end == -1:
            section_end = len(content)

        # 날짜 헤더 다음 줄에 추가
        header_end = content.find("\n", idx) + 1
        content = content[:header_end] + entry + "\n" + content[header_end:]
    else:
        # 새 날짜 섹션 생성
        insert_pos = content.find("---") + 3
        content = content[:insert_pos] + f"\n\n{date_header}\n\n{entry}\n" + content[insert_pos:]

    WORKLOG_PATH.write_text(content)

    # 상태 저장
    save_state({
        "surya_completed": current["surya_completed"],
        "clustered": current["clustered"],
        "last_update": now_str
    })

    print(f"[{now.strftime('%H:%M:%S')}] OCR: {current['surya_completed']:,}/{current['total']:,} ({surya_pct:.1f}%) [{change_text}]")


def show_status():
    """현재 상태 출력"""
    current = get_current_stats()
    if not current:
        print("DB를 찾을 수 없습니다.")
        return

    print("\n=== Surya Agent Status ===")
    print(f"전체 헤더:      {current['total']:,}")
    print(f"Surya OCR:     {current['surya_completed']:,} ({current['surya_completed']/current['total']*100:.1f}%)")
    print(f"클러스터:       {current['cluster_count']} 개")
    print(f"클러스터링됨:   {current['clustered']:,}")

    print("\n=== 유형별 진행률 ===")
    for jenis, data in current["by_jenis"].items():
        bar_len = int(data["pct"] / 5)  # 20칸 기준
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {jenis:8s} [{bar}] {data['done']:>6,}/{data['total']:>6,} ({data['pct']:5.1f}%)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        show_status()
    else:
        update_worklog()
