#!/usr/bin/env python3
"""
Codex 주기적 모니터링 스크립트
3분마다 Codex 세션을 스냅샷하고 기록합니다.

실행: nohup python3 codex_monitor_periodic.py &
"""

import json
import time
from pathlib import Path
from datetime import datetime

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
OUTPUT_LOG = Path.home() / "workspace" / "cor" / "logs" / "codex_activity.log"
INTERVAL_SECONDS = 180  # 3분


def get_latest_session() -> Path | None:
    """가장 최근 세션 파일 찾기"""
    sessions = list(CODEX_SESSIONS_DIR.rglob("*.jsonl"))
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def parse_event(line: str) -> dict | None:
    try:
        return json.loads(line.strip())
    except json.JSONDecodeError:
        return None


def format_event(event: dict) -> str | None:
    ts = event.get("timestamp", "")
    event_type = event.get("type", "")
    payload = event.get("payload", {})

    if event_type == "event_msg":
        msg_type = payload.get("type", "")
        if msg_type == "agent_reasoning":
            text = payload.get("text", "")
            return f"  🧠 {text[:150]}..."
        elif msg_type == "token_count":
            info = payload.get("info", {})
            total = info.get("total_token_usage", {})
            return f"  📊 토큰: in={total.get('input_tokens', 0):,}, out={total.get('output_tokens', 0):,}"

    elif event_type == "response_item":
        item_type = payload.get("type", "")
        if item_type == "function_call":
            name = payload.get("name", "")
            args = payload.get("arguments", "")
            try:
                args_dict = json.loads(args)
                cmd = args_dict.get("command", str(args_dict)[:80])
            except:
                cmd = args[:80]
            return f"  🔧 {name}: {cmd}"
        elif item_type == "function_call_output":
            output = payload.get("output", "")[:100].replace('\n', ' ')
            return f"  📤 {output}"

    return None


def log(message: str):
    OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    with open(OUTPUT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def take_snapshot():
    """현재 Codex 상태 스냅샷"""
    latest = get_latest_session()

    if not latest:
        log("❌ Codex 세션 없음")
        return

    mtime = datetime.fromtimestamp(latest.stat().st_mtime)
    age_seconds = (datetime.now() - mtime).total_seconds()

    # 세션이 5분 이상 업데이트 안 됐으면 비활성으로 판단
    if age_seconds > 300:
        log(f"⏸️  Codex 비활성 (마지막 업데이트: {int(age_seconds)}초 전)")
        return

    log(f"📁 세션: {latest.name[:50]}...")
    log(f"   크기: {latest.stat().st_size / 1024:.1f}KB, 업데이트: {int(age_seconds)}초 전")

    # 마지막 30줄에서 주요 이벤트 추출
    with open(latest, "r", encoding="utf-8") as f:
        lines = f.readlines()[-30:]

    events_logged = 0
    for line in lines:
        event = parse_event(line)
        if event:
            formatted = format_event(event)
            if formatted and events_logged < 5:  # 최대 5개 이벤트만
                log(formatted)
                events_logged += 1


def main():
    log("=" * 50)
    log("🚀 Codex 주기적 모니터링 시작 (3분 간격)")
    log("=" * 50)

    while True:
        try:
            take_snapshot()
            log("-" * 30)
        except Exception as e:
            log(f"❌ 오류: {e}")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
