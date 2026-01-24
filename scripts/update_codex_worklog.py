#!/usr/bin/env python3
"""Codex 작업 기록 자동 갱신 (1분마다 실행용)"""

import json
from datetime import datetime, timedelta
from pathlib import Path

WORKLOG_PATH = Path("/home/tylor/cor/docs/worklog.md")
STATE_FILE = Path("/home/tylor/cor/.codex_worklog_state.json")
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

SESSION_MAX_AGE_HOURS = 24


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_active_sessions():
    if not CODEX_SESSIONS.exists():
        return []

    cutoff = datetime.now() - timedelta(hours=SESSION_MAX_AGE_HOURS)
    sessions = []

    for f in CODEX_SESSIONS.rglob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except FileNotFoundError:
            continue
        if mtime > cutoff:
            sessions.append(f)

    return sessions


def parse_timestamp(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def summarize_tool(tool_name, args_text):
    if not args_text:
        return tool_name
    try:
        args = json.loads(args_text)
    except Exception:
        args = args_text

    if tool_name == "shell_command":
        cmd = ""
        if isinstance(args, dict):
            cmd = args.get("command", "")
        else:
            cmd = str(args)
        cmd = cmd.strip().replace("\n", " ")
        if len(cmd) > 80:
            cmd = cmd[:80] + "..."
        return f"`{cmd}`"

    if tool_name == "apply_patch":
        return "apply_patch"

    if isinstance(args, dict):
        keys = ",".join(sorted(args.keys()))
        return f"{tool_name} ({keys})" if keys else tool_name

    return tool_name


def extract_tool_uses(session_file, start_pos=0):
    tools = []

    with open(session_file, "r", encoding="utf-8") as f:
        f.seek(start_pos)
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") != "response_item":
                continue

            payload = event.get("payload", {})
            if payload.get("type") != "function_call":
                continue

            tool_name = payload.get("name", "unknown")
            args_text = payload.get("arguments", "")
            ts = parse_timestamp(event.get("timestamp", ""))
            summary = summarize_tool(tool_name, args_text)

            tools.append(
                {
                    "datetime": ts,
                    "tool": tool_name,
                    "summary": summary,
                    "session": session_file.stem[-8:],
                }
            )

        return tools, f.tell()


def update_worklog():
    sessions = get_active_sessions()
    if not sessions:
        return

    state = load_state()
    all_tools = []

    for session_file in sessions:
        last_pos = state.get(str(session_file), 0)
        tools, new_pos = extract_tool_uses(session_file, last_pos)
        if tools:
            all_tools.extend(tools)
            state[str(session_file)] = new_pos
        else:
            state[str(session_file)] = new_pos

    if not all_tools:
        save_state(state)
        return

    all_tools.sort(key=lambda x: x["datetime"] or datetime.min)

    if WORKLOG_PATH.exists():
        content = WORKLOG_PATH.read_text(encoding="utf-8")
    else:
        content = "# Work Log\n\nCodex/Claude Code 세션 작업 기록\n\n---\n"

    today = datetime.now().strftime("%Y-%m-%d")
    date_header = f"## {today}"

    new_entries = []
    for tool in all_tools:
        if tool["datetime"]:
            time_str = tool["datetime"].strftime("%H:%M:%S")
        else:
            time_str = "??:??:??"

        entry = (
            f"- **{time_str}** [codex:{tool['session']}] "
            f"[{tool['tool']}] {tool['summary']}"
        )
        new_entries.append(entry)

    entries_text = "\n".join(new_entries) + "\n"

    if date_header in content:
        idx = content.find(date_header) + len(date_header)
        next_newline = content.find("\n", idx)
        if next_newline != -1:
            content = content[: next_newline + 1] + entries_text + content[next_newline + 1 :]
    else:
        if "---" in content:
            idx = content.find("---") + 3
            content = content[:idx] + f"\n\n{date_header}\n{entries_text}" + content[idx:]
        else:
            content += f"\n{date_header}\n{entries_text}"

    WORKLOG_PATH.write_text(content, encoding="utf-8")
    save_state(state)


if __name__ == "__main__":
    update_worklog()
