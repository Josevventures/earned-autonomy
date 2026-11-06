"""ADAPTER: Claude Code CLI (``claude -p``). The only module that knows this runtime's tool names, flags and files.

What makes a lane's allowlist the real boundary in this runtime (verified against the CLI when the lanes were
built; re-verify against your version, these are its flags, not this package's):

- ``--permission-mode dontAsk`` refuses any tool not allowed, where an auto mode might approve it;
- ``--restricted`` ignores the user, project and local settings files, so their allow lines do not reach the job;
- ``--tools <list>`` leaves only the listed built-in tools in the session.

The deny list stays as a second layer. Push is blocked separately, in the job's environment
(``guard.pushblock``), because no command pattern catches every spelling of a push.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..guard.lanes import ALLOWLIST, DENYLIST, Lane, detect_sensitive_paths

READ_BUILTINS = ("Read", "Grep", "Glob", "WebSearch", "WebFetch", "ToolSearch")

# Built-in tools that write, run code, spawn work or act outside the job. Agent/Task are here because a subagent's
# isolation parameter (another checkout, a remote sandbox) cannot be matched by a permission rule. Skill is here
# because a skill is not a plain read.
WRITE_CAPABLE = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "Monitor", "Agent", "Task", "Skill",
    "SendMessage", "TaskStop", "EnterWorktree", "ExitWorktree", "CronCreate", "CronDelete", "RemoteTrigger",
    "Workflow", "Artifact",
})

READ_ONLY = Lane(name="read_only", allow=READ_BUILTINS, deny=tuple(sorted(WRITE_CAPABLE)), boundary=ALLOWLIST)

# The explicit-ask lane: the user asked for exactly this job, so internal reversible writes are allowed. It keeps
# the project's settings (and hooks), so its boundary is the deny list. Push is blocked in its environment.
PUSH_UNDO_DENY = tuple(
    f"{sh}({p})" for sh in ("Bash", "PowerShell")
    for p in ("git push*", "git -c protocol.*", "git -c url.*", "git -c credential.*", "git --config-env*",
              "env -u*", "env -i*", "GIT_ALLOW_PROTOCOL=*", "GIT_CONFIG*=*", "export GIT_*", "unset GIT_*",
              "GH_TOKEN=*", "GITHUB_TOKEN=*"))
ASKED = Lane(name="asked", allow=(*READ_BUILTINS, "Write", "Edit", "Bash", "Agent"), deny=PUSH_UNDO_DENY,
             boundary=DENYLIST)


def launch_flags(lane: Lane) -> list[str]:
    if lane.boundary == ALLOWLIST:
        builtins = list(dict.fromkeys(r.split("(", 1)[0] for r in lane.allow if not r.startswith("mcp__")))
        return ["--permission-mode", "dontAsk", "--restricted", "--tools", ",".join(builtins)]
    return ["--permission-mode", "dontAsk"]


def build_command(prompt: str, lane: Lane | None = None, extra_allowed: list[str] | None = None) -> list[str]:
    """The exact command for a job. Variadic flags come after the prompt so they cannot swallow it."""
    cmd = ["claude", "-p", "--output-format", "json", prompt]
    allowed = list(extra_allowed or [])
    deny: list[str] = []
    if lane:
        cmd += launch_flags(lane)
        allowed = [*lane.allow, *allowed]
        deny = list(lane.deny)
    if allowed:
        cmd += ["--allowed-tools", ",".join(dict.fromkeys(allowed))]
    if deny:
        cmd += ["--disallowed-tools", ",".join(deny)]
    return cmd


def parse_result(stdout: str, sensitive_markers: tuple[str, ...] = (".claude",)) -> dict:
    """Read the CLI's JSON result. Denials come from the structured ``permission_denials`` array, never from the
    model's prose: the prose phrasing of a denial varies between runs and a regex on it misses some."""
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "complete", "result": stdout.strip() or "(no output)", "denied_tools": []}
    result = (parsed.get("result") or "").strip() or "(no output)"
    denials = parsed.get("permission_denials") or []
    if denials:
        sensitive = detect_sensitive_paths(denials, sensitive_markers)
        return {"status": "permission_denied", "result": result,
                "denied_tools": list(dict.fromkeys(d.get("tool_name") for d in denials if d.get("tool_name"))),
                "sensitive_paths": sensitive,
                # a denial on a protected path cannot be fixed by a grant; offering one would loop
                "grantable": not sensitive}
    if parsed.get("is_error"):
        return {"status": "error", "result": result, "denied_tools": []}
    return {"status": "complete", "result": result, "denied_tools": []}


class SettingsFileBackend:
    """PermissionBackend over a settings JSON file's ``permissions.allow`` list. Pass the path explicitly."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")

    def allow(self, tool: str) -> bool:
        data = self._read()
        allow = data.setdefault("permissions", {}).setdefault("allow", [])
        if tool in allow:
            return False
        allow.append(tool)
        self._write(data)
        return True

    def revoke(self, tool: str) -> None:
        data = self._read()
        allow = data.get("permissions", {}).get("allow", [])
        if tool in allow:
            allow.remove(tool)
            self._write(data)

    def allow_rules(self) -> list[str]:
        return list(self._read().get("permissions", {}).get("allow", []))
