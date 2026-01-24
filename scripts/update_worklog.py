#!/usr/bin/env python3
"""워크로그 자동 갱신 - 모든 Claude Code 세션 작업 기록 (1분마다 실행)"""

import json
from datetime import datetime, timedelta
from pathlib import Path

WORKLOG_PATH = Path("/home/tylor/cor/docs/worklog.md")
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects" / "-home-tylor"
STATE_FILE = Path("/home/tylor/cor/.worklog_state.json")

# 최근 24시간 내 수정된 세션만 처리
SESSION_MAX_AGE_HOURS = 24


def load_state():
    """세션별 마지막 처리 위치 로드"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {}


def save_state(state):
    """세션별 마지막 처리 위치 저장"""
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_active_sessions():
    """최근 수정된 모든 세션 파일 반환"""
    if not CLAUDE_PROJECTS.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=SESSION_MAX_AGE_HOURS)
    sessions = []

    for f in CLAUDE_PROJECTS.glob("*.jsonl"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime > cutoff:
            sessions.append(f)

    return sessions


def extract_tool_uses(session_file, start_line=0):
    """세션 파일에서 도구 사용 기록 추출"""
    tools = []

    with open(session_file, 'r') as f:
        for i, line in enumerate(f):
            if i < start_line:
                continue

            try:
                data = json.loads(line)

                # assistant 메시지 내 tool_use 찾기
                if data.get("type") == "assistant":
                    message = data.get("message", {})
                    content = message.get("content", [])
                    timestamp = data.get("timestamp", "")

                    # 타임스탬프 파싱
                    dt = None
                    if timestamp:
                        try:
                            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        except:
                            pass

                    for item in content:
                        if item.get("type") == "tool_use":
                            tool_name = item.get("name", "unknown")
                            input_data = item.get("input", {})

                            # 도구별 요약
                            summary = format_tool_summary(tool_name, input_data)

                            tools.append({
                                "line": i,
                                "datetime": dt,
                                "tool": tool_name,
                                "summary": summary,
                                "session": session_file.stem[:8]  # 세션 ID 앞 8자
                            })

            except json.JSONDecodeError:
                continue

    return tools


def format_tool_summary(tool_name, input_data):
    """도구 사용 요약 포맷팅"""
    if tool_name == "Bash":
        cmd = input_data.get("command", "")[:50]
        return f"`{cmd}...`" if len(input_data.get("command", "")) > 50 else f"`{cmd}`"
    elif tool_name == "Edit":
        fp = input_data.get("file_path", "")
        return f"수정: `{Path(fp).name}`"
    elif tool_name == "Write":
        fp = input_data.get("file_path", "")
        return f"생성: `{Path(fp).name}`"
    elif tool_name == "Read":
        fp = input_data.get("file_path", "")
        return f"읽기: `{Path(fp).name}`"
    elif tool_name == "Glob":
        pattern = input_data.get("pattern", "")
        return f"검색: `{pattern}`"
    elif tool_name == "Grep":
        pattern = input_data.get("pattern", "")[:30]
        return f"검색: `{pattern}`"
    elif tool_name == "Task":
        desc = input_data.get("description", "")[:30]
        return f"작업: {desc}"
    elif tool_name == "AskUserQuestion":
        return "질문"
    else:
        return tool_name


def update_worklog():
    """모든 활성 세션에서 워크로그 업데이트"""
    sessions = get_active_sessions()
    if not sessions:
        return

    state = load_state()
    all_tools = []

    # 모든 세션에서 새 도구 사용 수집
    for session_file in sessions:
        session_id = session_file.name
        last_line = state.get(session_id, 0)

        tools = extract_tool_uses(session_file, last_line)

        if tools:
            all_tools.extend(tools)
            # 마지막 처리 라인 업데이트
            state[session_id] = tools[-1]["line"] + 1

    if not all_tools:
        return

    # 시간순 정렬
    all_tools.sort(key=lambda x: x["datetime"] or datetime.min)

    # 워크로그 파일 읽기
    if WORKLOG_PATH.exists():
        content = WORKLOG_PATH.read_text()
    else:
        content = "# Work Log\n\nClaude Code 세션 작업 기록 (1분마다 자동 갱신)\n\n---\n"

    # 날짜별로 그룹화
    today = datetime.now().strftime("%Y-%m-%d")
    date_header = f"## {today}"

    # 새 기록 생성
    new_entries = []
    for tool in all_tools:
        if tool["datetime"]:
            time_str = tool["datetime"].strftime("%H:%M:%S")
        else:
            time_str = "??:??:??"

        entry = f"- **{time_str}** [{tool['session']}] [{tool['tool']}] {tool['summary']}"
        new_entries.append(entry)

    entries_text = "\n".join(new_entries) + "\n"

    # 날짜 섹션에 추가
    if date_header in content:
        idx = content.find(date_header) + len(date_header)
        next_newline = content.find("\n", idx)
        if next_newline != -1:
            content = content[:next_newline+1] + entries_text + content[next_newline+1:]
    else:
        # 새 날짜 섹션 추가
        if "---" in content:
            idx = content.find("---") + 3
            content = content[:idx] + f"\n\n{date_header}\n{entries_text}" + content[idx:]
        else:
            content += f"\n{date_header}\n{entries_text}"

    WORKLOG_PATH.write_text(content)
    save_state(state)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {len(all_tools)}개 기록 (세션 {len(sessions)}개)")


def clean_old_state():
    """오래된 세션 상태 정리"""
    state = load_state()
    active_sessions = {f.name for f in get_active_sessions()}

    # 더 이상 활성이 아닌 세션 제거
    cleaned = {k: v for k, v in state.items() if k in active_sessions}

    if len(cleaned) < len(state):
        save_state(cleaned)


if __name__ == "__main__":
    update_worklog()
    clean_old_state()
