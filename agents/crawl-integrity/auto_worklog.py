#!/usr/bin/env python3
"""
크롤링 정합성 체크 에이전트 - 자동 워크로그 기록
1분마다 실행되어 에이전트의 작업 상태를 기록
"""

import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# 경로 설정
AGENT_DIR = Path("/home/tylor/cor/agents/crawl-integrity")
WORKLOG_MD = AGENT_DIR / "worklog.md"
WORKLOG_JSONL = AGENT_DIR / "worklog.jsonl"
STATE_FILE = AGENT_DIR / ".state.json"

# DB 경로
MAIN_DB = Path("/home/tylor/cor/peraturan/data/db/peraturan.db")
CLI_DB = Path("/home/tylor/cor/data/peraturan.db")

# KOICA 기준
KOICA_TARGETS = {
    'UUD': ('헌법', 1),
    'TAP MPR': ('국민협의회 결의', 41),
    'UU': ('법률', 1902),
    'UUDRT': ('긴급법령', 177),
    'UUDS': ('임시헌법', 1),
    'PERPPU': ('대체법령', 217),
    'PP': ('정부령', 4939),
    'PERPRES': ('대통령령', 2580),
    'PENPRES': ('대통령 확정', 76),
    'PERMEN': ('장관령', 18880),
    'PERBAN': ('기관/단체 규정', 6242),
    'TERJEMAH': ('번역 법령', 368),
}

JENIS_TO_CODE = {
    'UNDANG-UNDANG': 'UU',
    'PERATURAN PEMERINTAH': 'PP',
    'PERATURAN PRESIDEN': 'PERPRES',
    'PERATURAN MENTERI': 'PERMEN',
    'PERATURAN BADAN/LEMBAGA': 'PERBAN',
    'PERATURAN PEMERINTAH PENGGANTI UNDANG-UNDANG': 'PERPPU',
    'KETETAPAN MAJELIS PERMUSYAWARATAN RAKYAT': 'TAP MPR',
    'PENETAPAN PRESIDEN': 'PENPRES',
}


@dataclass
class AgentState:
    """에이전트 상태"""
    current_task: str = ""
    task_progress: str = ""
    last_action: str = ""
    pending_tasks: list = None
    completed_tasks: list = None
    issues_found: list = None

    def __post_init__(self):
        self.pending_tasks = self.pending_tasks or []
        self.completed_tasks = self.completed_tasks or []
        self.issues_found = self.issues_found or []


def load_state() -> AgentState:
    """이전 상태 로드"""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return AgentState(**data)
        except:
            pass
    return AgentState()


def save_state(state: AgentState):
    """상태 저장"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2))


def get_db_counts(db_path: Path) -> dict:
    """DB에서 법령 유형별 건수 조회"""
    if not db_path.exists():
        return {}

    counts = {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # jenis별 통계
        cur.execute("SELECT jenis, COUNT(*) FROM peraturan GROUP BY jenis")
        for jenis, count in cur.fetchall():
            code = JENIS_TO_CODE.get(jenis, jenis)
            counts[code] = counts.get(code, 0) + count

        # 특수 유형 (slug 패턴)
        cur.execute("SELECT COUNT(*) FROM peraturan WHERE slug LIKE 'uudrt-%'")
        counts['UUDRT'] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM peraturan WHERE slug LIKE 'uud-1945%' OR jenis = 'UUD'")
        counts['UUD'] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM peraturan WHERE slug LIKE 'terjemah-%'")
        counts['TERJEMAH'] = cur.fetchone()[0]

        conn.close()
    except Exception as e:
        counts['_error'] = str(e)

    return counts


def get_running_tasks() -> list[dict]:
    """실행 중인 관련 작업 조회"""
    tasks = []
    keywords = ["crawl", "download", "pengundangan", "peraturan"]

    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.split("\n"):
            line_lower = line.lower()
            if any(kw in line_lower for kw in keywords) and "python" in line_lower:
                if "ps aux" in line or "grep" in line:
                    continue
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    tasks.append({
                        "pid": parts[1],
                        "cpu": parts[2],
                        "command": parts[10][:80]
                    })
    except:
        pass

    return tasks


def analyze_integrity() -> dict:
    """정합성 분석"""
    main_counts = get_db_counts(MAIN_DB)
    cli_counts = get_db_counts(CLI_DB)

    analysis = {
        "timestamp": datetime.now().isoformat(),
        "main_db": {"path": str(MAIN_DB), "counts": main_counts},
        "cli_db": {"path": str(CLI_DB), "counts": cli_counts},
        "koica_status": {},
        "total_missing": 0,
        "issues": []
    }

    # KOICA 정합성 체크
    for code, (name, target) in KOICA_TARGETS.items():
        # 메인 DB + CLI DB 합산 (중복 고려 안함 - 나중에 병합)
        main_count = main_counts.get(code, 0)
        cli_count = cli_counts.get(code, 0)

        # CLI에만 있는 경우 (아직 병합 안됨)
        if cli_count > 0 and main_count == 0:
            current = cli_count
            note = "CLI DB (병합 대기)"
        else:
            current = main_count
            note = "메인 DB"

        diff = current - target
        if diff >= 0:
            status = "OK"
        elif diff >= -10:
            status = "WARN"
        else:
            status = "MISSING"
            analysis["issues"].append(f"{code}: {abs(diff)}건 누락")

        if diff < 0:
            analysis["total_missing"] += abs(diff)

        analysis["koica_status"][code] = {
            "name": name,
            "target": target,
            "current": current,
            "diff": diff,
            "status": status,
            "note": note
        }

    return analysis


def detect_changes(prev_state: AgentState, analysis: dict) -> list[str]:
    """변경 사항 감지"""
    changes = []

    # 새로 완료된 작업 감지
    running = get_running_tasks()
    running_cmds = [t["command"] for t in running]

    # UUDRT 크롤링 완료 감지
    uudrt_status = analysis["koica_status"].get("UUDRT", {})
    if uudrt_status.get("current", 0) >= 175:
        if "UUDRT 크롤링" not in prev_state.completed_tasks:
            changes.append(f"✅ UUDRT 크롤링 완료 ({uudrt_status.get('current')}/177)")

    return changes


def update_worklog_md(analysis: dict, state: AgentState, changes: list[str]):
    """마크다운 워크로그 업데이트"""
    ts = analysis["timestamp"]

    # 상태 아이콘
    total_missing = analysis["total_missing"]
    if total_missing == 0:
        status_icon = "🟢"
        status_text = "정합성 완료"
    elif total_missing < 50:
        status_icon = "🟡"
        status_text = "경미한 누락"
    else:
        status_icon = "🔴"
        status_text = f"{total_missing}건 누락"

    # KOICA 테이블
    koica_lines = []
    for code, (name, _) in KOICA_TARGETS.items():
        s = analysis["koica_status"].get(code, {})
        target = s.get("target", 0)
        current = s.get("current", 0)
        diff = s.get("diff", 0)
        status = s.get("status", "?")
        note = s.get("note", "")

        icon = "✅" if status == "OK" else ("⚠️" if status == "WARN" else "❌")
        koica_lines.append(f"| {code} | {name} | {target} | {current} | {diff:+d} | {icon} | {note} |")

    # 실행 중인 작업
    running = get_running_tasks()
    running_lines = []
    for t in running[:5]:
        cmd = t.get("command", "")[:60]
        running_lines.append(f"| {t.get('pid')} | {t.get('cpu')}% | {cmd}... |")

    # 변경 이력 (최근 10개)
    history_file = AGENT_DIR / ".history.jsonl"
    recent_changes = []
    if changes:
        # 새 변경 사항 추가
        with open(history_file, "a") as f:
            for c in changes:
                f.write(json.dumps({"ts": ts, "change": c}, ensure_ascii=False) + "\n")

    # 최근 변경 이력 읽기
    if history_file.exists():
        lines = history_file.read_text().strip().split("\n")[-10:]
        for line in reversed(lines):
            try:
                data = json.loads(line)
                recent_changes.append(f"- `{data['ts'][:19]}` {data['change']}")
            except:
                pass

    md = f"""# 크롤링 정합성 에이전트 워크로그

> **마지막 업데이트**: {ts}
> **상태**: {status_icon} {status_text}

## 현재 작업

**{state.current_task or '대기 중'}**
{state.task_progress}

## KOICA 정합성 현황

| 약어 | 법령명 | 기준 | 현재 | 차이 | 상태 | 비고 |
|------|--------|------|------|------|------|------|
{chr(10).join(koica_lines)}

**총 누락: {total_missing}건**

## 실행 중인 작업

| PID | CPU | Command |
|-----|-----|---------|
{chr(10).join(running_lines) if running_lines else "| - | - | (없음) |"}

## 최근 변경 이력

{chr(10).join(recent_changes) if recent_changes else "- (없음)"}

## 대기 중인 작업

{chr(10).join(f"- [ ] {t}" for t in state.pending_tasks) if state.pending_tasks else "- (없음)"}

## 완료된 작업

{chr(10).join(f"- [x] {t}" for t in state.completed_tasks[-5:]) if state.completed_tasks else "- (없음)"}

---

*자동 생성: {ts}*
"""

    WORKLOG_MD.write_text(md)


def main():
    """메인 실행"""
    AGENT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[{datetime.now().isoformat()}] 크롤링 정합성 에이전트 워크로그 기록...")

    # 이전 상태 로드
    state = load_state()

    # 정합성 분석
    analysis = analyze_integrity()

    # 변경 사항 감지
    changes = detect_changes(state, analysis)

    # 상태 업데이트
    if changes:
        for c in changes:
            if "완료" in c and c not in state.completed_tasks:
                state.completed_tasks.append(c.replace("✅ ", ""))
        state.last_action = changes[-1] if changes else state.last_action

    # UUDRT 병합 대기 작업 추가
    uudrt = analysis["koica_status"].get("UUDRT", {})
    if uudrt.get("note") == "CLI DB (병합 대기)" and "CLI DB → 메인 DB 병합" not in state.pending_tasks:
        state.pending_tasks.append("CLI DB → 메인 DB 병합 (UUDRT)")

    # 워크로그 업데이트
    update_worklog_md(analysis, state, changes)

    # JSONL 기록
    with open(WORKLOG_JSONL, "a") as f:
        f.write(json.dumps(analysis, ensure_ascii=False) + "\n")

    # 상태 저장
    save_state(state)

    # 콘솔 출력
    print(f"  총 누락: {analysis['total_missing']}건")
    if changes:
        for c in changes:
            print(f"  {c}")
    print(f"  저장: {WORKLOG_MD}")


if __name__ == "__main__":
    main()
