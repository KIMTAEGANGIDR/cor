#!/usr/bin/env python3
"""
Codex 작업 모니터링 및 기록 스크립트

실시간으로 Codex 세션 로그를 감시하고 주요 이벤트를 기록합니다.
실행: python3 codex_monitor.py [--watch]
"""

import json
import time
import argparse
from pathlib import Path
from datetime import datetime

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
OUTPUT_LOG = Path.home() / "workspace" / "cor" / "logs" / "codex_activity.log"


def get_latest_session() -> Path | None:
    """가장 최근 세션 파일 찾기"""
    sessions = list(CODEX_SESSIONS_DIR.rglob("*.jsonl"))
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def parse_event(line: str) -> dict | None:
    """이벤트 라인 파싱"""
    try:
        return json.loads(line.strip())
    except json.JSONDecodeError:
        return None


def format_event(event: dict) -> str | None:
    """이벤트를 사람이 읽기 쉬운 형태로 변환"""
    ts = event.get("timestamp", "")
    event_type = event.get("type", "")
    payload = event.get("payload", {})

    if event_type == "event_msg":
        msg_type = payload.get("type", "")

        if msg_type == "agent_reasoning":
            text = payload.get("text", "")
            return f"[{ts}] 🧠 추론: {text[:200]}..."

        elif msg_type == "token_count":
            info = payload.get("info", {})
            total = info.get("total_token_usage", {})
            return f"[{ts}] 📊 토큰: input={total.get('input_tokens', 0):,}, output={total.get('output_tokens', 0):,}"

    elif event_type == "response_item":
        item_type = payload.get("type", "")

        if item_type == "function_call":
            name = payload.get("name", "")
            args = payload.get("arguments", "")
            try:
                args_dict = json.loads(args)
                cmd = args_dict.get("command", args_dict)
            except:
                cmd = args[:100]
            return f"[{ts}] 🔧 실행: {name} - {cmd}"

        elif item_type == "function_call_output":
            output = payload.get("output", "")[:200]
            return f"[{ts}] 📤 결과: {output}"

        elif item_type == "reasoning":
            summaries = payload.get("summary", [])
            if summaries:
                text = summaries[0].get("text", "")[:200]
                return f"[{ts}] 💭 요약: {text}..."

    elif event_type == "turn_context":
        model = payload.get("model", "unknown")
        return f"[{ts}] 🔄 컨텍스트: model={model}"

    return None


def log_event(message: str):
    """이벤트를 로그 파일에 기록"""
    OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_LOG, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    print(message)


def process_session_file(session_file: Path, start_pos: int = 0) -> int:
    """세션 파일 처리, 새 위치 반환"""
    with open(session_file, "r", encoding="utf-8") as f:
        f.seek(start_pos)
        for line in f:
            event = parse_event(line)
            if event:
                formatted = format_event(event)
                if formatted:
                    log_event(formatted)
        return f.tell()


def watch_mode():
    """실시간 감시 모드"""
    print("🔍 Codex 모니터링 시작... (Ctrl+C로 종료)")
    log_event(f"\n{'='*60}")
    log_event(f"모니터링 시작: {datetime.now().isoformat()}")
    log_event(f"{'='*60}")

    current_session = None
    file_pos = 0

    while True:
        latest = get_latest_session()

        if latest and latest != current_session:
            current_session = latest
            file_pos = 0
            log_event(f"\n📁 새 세션 감지: {current_session.name}")

        if current_session and current_session.exists():
            new_pos = process_session_file(current_session, file_pos)
            if new_pos > file_pos:
                file_pos = new_pos

        time.sleep(2)


def snapshot_mode():
    """현재 상태 스냅샷"""
    latest = get_latest_session()
    if not latest:
        print("세션 파일 없음")
        return

    print(f"📁 세션: {latest.name}")
    print(f"📊 크기: {latest.stat().st_size / 1024:.1f} KB")
    print(f"⏰ 수정: {datetime.fromtimestamp(latest.stat().st_mtime)}")
    print("\n최근 활동:")
    print("-" * 40)

    # 마지막 50줄만 처리
    with open(latest, "r", encoding="utf-8") as f:
        lines = f.readlines()[-50:]

    for line in lines:
        event = parse_event(line)
        if event:
            formatted = format_event(event)
            if formatted:
                print(formatted)


def main():
    parser = argparse.ArgumentParser(description="Codex 모니터링")
    parser.add_argument("--watch", "-w", action="store_true", help="실시간 감시 모드")
    args = parser.parse_args()

    if args.watch:
        try:
            watch_mode()
        except KeyboardInterrupt:
            print("\n모니터링 종료")
    else:
        snapshot_mode()


if __name__ == "__main__":
    main()
