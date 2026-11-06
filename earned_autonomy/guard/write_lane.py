"""The scoped write lane: read-only, plus writes limited to the folders a workflow declares as its output home.

The bug it closes: a request that read like analysis ("review how last week's report performed") was routed to
the read-only lane, but the workflow it ran ends by saving a report, so the job finished with "I couldn't save
it". Prompt wording had decided the lane. Now the workflow declares where it saves, in its own frontmatter:

    ---
    name: weekly-report
    outputs:
      - reports/weekly/
    ---

and the lane is computed from that declaration: the read-only lane plus ``Write(<dir>/**)`` and ``Edit(<dir>/**)``
for each declared folder. A folder outside the allowed roots, or naming a private, local or config path, or
carrying a glob, is dropped. A workflow that also declares ``allowed-tools`` is refused this lane (it would gain
tools beyond the scope while it runs) and the caller falls back to asking the user.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .lanes import Lane

DEFAULT_ALLOWED_ROOTS = ("reports/", "notes/", "drafts/")
DEFAULT_FORBIDDEN_PARTS = frozenset({"private", ".local", ".git", "..", "state", "secrets"})
_GLOB_CHARS = set("*?[]{}")


def frontmatter(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    out = []
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        out.append(line)
    return []


def list_key(fm: list[str], key: str) -> list[str]:
    """A block list (``key:`` then ``  - item`` lines) or an inline ``[a, b]`` list. Quotes are stripped."""
    vals, inside = [], False
    for line in fm:
        if re.match(rf"^{re.escape(key)}\s*:", line):
            rest = line.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                return [v.strip().strip("'\"") for v in rest[1:-1].split(",") if v.strip()]
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s+(.*)$", line)
            if m:
                vals.append(m.group(1).strip().strip("'\""))
            elif line.strip() and not line.startswith((" ", "\t")):
                inside = False
    return vals


def valid_output_dir(d: str, allowed_roots=DEFAULT_ALLOWED_ROOTS,
                     forbidden_parts=DEFAULT_FORBIDDEN_PARTS) -> str | None:
    """The folder normalized to ``a/b/``, or None when it is absolute, outside the allowed roots, names a
    forbidden part, or carries a glob."""
    d = (d or "").replace("\\", "/").strip()
    if not d or d.startswith("/") or re.match(r"^[A-Za-z]:", d) or set(d) & _GLOB_CHARS:
        return None
    d = d.rstrip("/") + "/"
    if not d.startswith(tuple(allowed_roots)):
        return None
    if any(p in forbidden_parts for p in d.split("/")):
        return None
    return d


def declared_outputs(workflow_text: str, **kw) -> tuple[list[str], bool]:
    """(valid output folders, whether the workflow declares allowed-tools)."""
    fm = frontmatter(workflow_text)
    outs = [v for v in (valid_output_dir(o, **kw) for o in list_key(fm, "outputs")) if v]
    return outs, any(re.match(r"^allowed-tools\s*:", ln) for ln in fm)


class LaneRefused(PermissionError):
    pass


def scoped_write_lane(read_lane: Lane, outputs: list[str], declares_allowed_tools: bool,
                      write_tools: tuple[str, ...] = ("Write", "Edit")) -> Lane:
    if declares_allowed_tools:
        raise LaneRefused("the workflow declares allowed-tools, which would widen the lane while it runs; "
                          "ask the user instead")
    if not outputs:
        raise LaneRefused("the workflow declares no valid output folder; it belongs in the read-only lane")
    scoped = tuple(f"{t}({d}**)" for d in outputs for t in write_tools)
    # Deny beats allow, so the bare write denies must go or they would cancel the scoped rules. Everything else on
    # the read-only deny list (shells, subagents, sends, deletes) stays.
    deny = tuple(t for t in read_lane.deny if t not in write_tools)
    return replace(read_lane, name="scoped_write", allow=(*read_lane.allow, *scoped), deny=deny)
