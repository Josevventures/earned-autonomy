"""Regression guard for "the read-only lane that could write".

In the system this was extracted from, a read-only job had Write refused and then wrote a file with the
PowerShell tool. The lane's deny list named Bash but not PowerShell, the ambient settings allowed PowerShell, and
the permission mode let anything not denied through. A deny list was the only barrier, and it had a gap.

The fix: the allowlist is the boundary (the job sees only the lane's tools and ignores ambient settings), the deny
list is a second layer, and ``audit`` checks a lane against the ambient allow rules.
"""
import json

from earned_autonomy.adapters import claude_code as cc
from earned_autonomy.guard.lanes import ALLOWLIST, DENYLIST, Lane, audit, detect_sensitive_paths, reachable

AMBIENT = ["Read", "Grep", "PowerShell", "Bash", "Write", "mcp__calendar__update_event"]


def test_the_leaky_shape_is_caught_by_the_audit():
    leaky = Lane("read_only_v1", allow=("Read", "Grep"), deny=("Write", "Edit", "Bash"), boundary=DENYLIST)
    assert audit(leaky, AMBIENT, cc.WRITE_CAPABLE) == ["PowerShell"]


def test_an_allowlist_boundary_ignores_ambient_allow_rules():
    lane = Lane("read_only", allow=("Read", "Grep"), deny=(), boundary=ALLOWLIST)
    assert reachable(lane, AMBIENT) == ["Read", "Grep"]
    assert audit(lane, AMBIENT, cc.WRITE_CAPABLE) == []


def test_the_shipped_read_only_lane_holds_against_every_write_capable_ambient_rule():
    ambient = sorted(cc.WRITE_CAPABLE) + ["Read"]
    assert audit(cc.READ_ONLY, ambient, cc.WRITE_CAPABLE) == []


def test_the_deny_list_still_names_every_write_capable_tool_as_a_second_layer():
    assert set(cc.WRITE_CAPABLE) <= set(cc.READ_ONLY.deny)
    assert {"PowerShell", "Bash", "Agent", "Skill"} <= set(cc.READ_ONLY.deny)


def test_nothing_the_read_only_lane_allows_is_write_capable():
    assert not set(cc.READ_ONLY.allow) & cc.WRITE_CAPABLE


def test_the_read_only_command_launches_with_the_allowlist_as_the_boundary():
    cmd = cc.build_command("summarize the notes folder", cc.READ_ONLY)
    i = cmd.index("--tools")
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--restricted" in cmd
    assert set(cmd[i + 1].split(",")) == set(cc.READ_BUILTINS)
    assert cmd[4] == "summarize the notes folder"          # the prompt precedes every variadic flag


def test_the_asked_lane_refuses_unlisted_tools_but_keeps_project_settings():
    cmd = cc.build_command("do the thing", cc.ASKED)
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--restricted" not in cmd
    assert any(r.startswith("Bash(git push") for r in cc.ASKED.deny)


def test_denials_come_from_the_structured_field_not_the_prose():
    out = json.dumps({"result": "I could not finish.", "permission_denials": [
        {"tool_name": "Write", "tool_input": {"file_path": "notes/a.md"}},
        {"tool_name": "Write", "tool_input": {"file_path": "notes/b.md"}},
        {"tool_name": "PowerShell", "tool_input": {"command": "x"}}]})
    r = cc.parse_result(out)
    assert r["status"] == "permission_denied" and r["denied_tools"] == ["Write", "PowerShell"]
    assert r["grantable"] is True


def test_a_denial_on_a_protected_path_is_not_grantable():
    out = json.dumps({"result": "", "permission_denials": [
        {"tool_name": "Edit", "tool_input": {"file_path": "C:\\work\\.claude\\settings.json"}}]})
    assert cc.parse_result(out)["grantable"] is False


def test_sensitive_paths_match_either_separator():
    """A check built with os.sep matched one separator per OS and missed the other on Linux CI."""
    denials = [{"tool_input": {"file_path": "C:\\repo\\.claude\\settings.json"}},
               {"tool_input": {"file_path": "/home/u/repo/.claude/hooks/x.sh"}},
               {"tool_input": {"file_path": ".claude/agents/a.md"}},
               {"tool_input": {"file_path": "/repo/not.claude/x"}},
               {"tool_input": {"command": "ls"}}]
    assert detect_sensitive_paths(denials) == ["C:\\repo\\.claude\\settings.json", "/home/u/repo/.claude/hooks/x.sh",
                                               ".claude/agents/a.md"]


def test_unparsed_output_is_reported_as_is():
    assert cc.parse_result("plain text")["result"] == "plain text"
