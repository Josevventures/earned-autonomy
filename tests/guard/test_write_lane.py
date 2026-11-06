import pytest

from earned_autonomy.adapters import claude_code as cc
from earned_autonomy.guard.lanes import audit
from earned_autonomy.guard.write_lane import LaneRefused, declared_outputs, scoped_write_lane, valid_output_dir

WORKFLOW = """---
name: weekly-report
outputs:
  - reports/weekly/
  - "notes/review"
description: saves a weekly report
---
body
"""


def test_declared_outputs_are_read_from_frontmatter():
    assert declared_outputs(WORKFLOW) == (["reports/weekly/", "notes/review/"], False)


def test_an_inline_list_works():
    assert declared_outputs("---\noutputs: [reports/a, 'drafts/b/']\n---\n")[0] == ["reports/a/", "drafts/b/"]


@pytest.mark.parametrize("bad", ["/etc/x", "C:/x", "src/", "reports/../secrets/", "reports/private/",
                                 "reports/*", "reports/.git/", "", "notes/state/"])
def test_bad_output_folders_are_dropped(bad):
    assert valid_output_dir(bad) is None


def test_the_scoped_lane_adds_scoped_writes_and_keeps_shells_and_subagents_denied():
    outs, has_tools = declared_outputs(WORKFLOW)
    lane = scoped_write_lane(cc.READ_ONLY, outs, has_tools)
    assert "Write(reports/weekly/**)" in lane.allow and "Edit(notes/review/**)" in lane.allow
    assert "Write" not in lane.deny and "Edit" not in lane.deny     # a bare deny would cancel the scoped allow
    assert {"Bash", "PowerShell", "Agent", "Skill"} <= set(lane.deny)
    assert audit(lane, ["Bash", "PowerShell", "Write"], cc.WRITE_CAPABLE) == []


def test_a_workflow_that_declares_allowed_tools_is_refused():
    text = WORKFLOW.replace("description:", "allowed-tools: Bash\ndescription:")
    outs, has_tools = declared_outputs(text)
    with pytest.raises(LaneRefused):
        scoped_write_lane(cc.READ_ONLY, outs, has_tools)


def test_no_valid_output_means_no_write_lane():
    with pytest.raises(LaneRefused):
        scoped_write_lane(cc.READ_ONLY, [], False)
