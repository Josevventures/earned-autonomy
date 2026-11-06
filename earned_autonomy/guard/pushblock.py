"""Block git push (and every other git transport) for a dispatched job, at the environment level.

Pattern-matching commands does not work: ``git push`` has too many spellings (``git -C dir push``, ``cd x; git
push``, ``send-pack``, ``gh``, a nested agent that runs it for you). Git configuration passed through the
environment applies to every git the job and its children start, so the block lives there:

- ``protocol.allow=never`` plus ``protocol.<name>.allow=never`` for each transport. The per-protocol keys matter:
  they beat ``protocol.allow``, so without them a repo-level ``[protocol "file"] allow = always`` reopens one.
- ``GIT_ALLOW_PROTOCOL`` set to a name that does not exist, which beats any ``-c protocol.*`` override.
- a ``pushInsteadOf`` that rewrites every push URL to a dead scheme, and an empty credential helper.
- for ``gh``: tokens removed (not poisoned: a bad token breaks anonymous reads) and an empty config dir.

The job can still undo its own environment if it has a shell and means to; the caller's deny rules cover the
obvious spellings. This blocks accidents and ordinary pushes, not a determined adversary with a shell.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

DEAD_PROTOCOL = "earned-autonomy-none"
MARKER = "AGENT_PUSH_BLOCKED"
TRANSPORTS = ("https", "http", "ssh", "git", "file", "ext")
PUSH_BLOCK_GIT = (("protocol.allow", "never"),
                  *((f"protocol.{p}.allow", "never") for p in TRANSPORTS),
                  ("url.earned-autonomy-no-push://blocked/.pushInsteadOf", ""),
                  ("credential.helper", ""))
GH_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def empty_dir(name: str = "earned-autonomy-gh-empty") -> str:
    d = Path(tempfile.gettempdir()) / name
    d.mkdir(parents=True, exist_ok=True)
    for p in d.iterdir():
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    return str(d)


def block_push(env: dict | None = None, gh_config_dir: str | None = None) -> dict:
    """Return a copy of ``env`` (default: this process's) with every git transport and gh auth blocked. Never
    mutates os.environ: the supervising process keeps its own ability to push."""
    env = dict(os.environ if env is None else env)
    env[MARKER] = "1"
    env["GIT_ALLOW_PROTOCOL"] = DEAD_PROTOCOL
    try:
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    for k, v in PUSH_BLOCK_GIT:
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
        n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    for t in GH_TOKENS:
        env.pop(t, None)
    env["GH_CONFIG_DIR"] = gh_config_dir or empty_dir()
    env["GH_PROMPT_DISABLED"] = "1"
    return env
