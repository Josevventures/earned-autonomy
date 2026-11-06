"""One-shot permission grants on dispatched jobs, fail-closed.

A headless job runs, hits a tool it was not allowed, and stops with a list of denied tools. The user can grant one
of them and the job re-runs. Each rule here closed a real hole:

- **A grant never widens a lane.** A job in a restricted lane is refused outright (``blocked``). Rebuilding the
  lane on the re-run is how a grant once dropped it and re-ran a read-only job unrestricted.
- **The re-run is the job's own request**, never text the client sends back.
- **Only a tool the job was actually denied**, and only while the job is waiting on a permission.
- **Consumed once.** The claim moves the job to ``granting`` under a lock, so a double click, a double POST or a
  concurrent grant finds it no longer waiting and is refused.
- **Fail closed after the claim.** Any failure between the claim and the re-run marks the job expired; nothing is
  undone and nothing re-runs.
- **A restart expires every waiting job.** The persisted copy keeps only the first 300 characters of a request,
  so it can never be re-run from disk. A restricted job still reads ``blocked`` after a restart.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from .lanes import Lane

EXPIRED = "expired"
BLOCKED = "blocked"
WRONG_TOOL = "wrong_tool"
PERSISTED_REQUEST_CHARS = 300


class JobBook:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------------------------------------------------
    def create(self, request: str, lane: Lane | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        self._jobs[job_id] = {"request": request, "status": "running", "lane": lane.name if lane else None,
                              "restricted": bool(lane and lane.restricted)}
        self.save()
        return job_id

    def get(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        return dict(job) if job else None

    def mark_denied(self, job_id: str, denied_tools: list[str]) -> None:
        self._update(job_id, status="permission_denied", deniedTools=list(dict.fromkeys(denied_tools)))

    def mark_done(self, job_id: str, status: str = "complete") -> None:
        self._update(job_id, status=status)

    def _update(self, job_id: str, **fields) -> None:
        self._jobs[job_id] = {**self._jobs[job_id], **fields}
        self.save()

    # -- grants ----------------------------------------------------------------------------------------------------
    def grant_check(self, job_id: str | None) -> tuple[str | None, str | None]:
        """(request to re-run, None) or (None, refusal reason)."""
        job = self._jobs.get(job_id) if job_id else None
        if not job or "restricted" not in job:
            return None, EXPIRED
        if job["restricted"] is not False:            # checked before from_disk: restricted stays "blocked"
            return None, BLOCKED
        if job.get("from_disk"):
            return None, EXPIRED
        if job.get("status") != "permission_denied" or not job.get("request"):
            return None, EXPIRED
        return job["request"], None

    def claim_grant(self, job_id: str | None, tool: str) -> tuple[str | None, str | None]:
        with self._lock:
            request, refusal = self.grant_check(job_id)
            if refusal:
                return None, refusal
            if tool not in (self._jobs[job_id].get("deniedTools") or []):
                return None, WRONG_TOOL
            self._update(job_id, status="granting")
            return request, None

    def finish_grant(self, job_id: str, tool: str, rerun_id: str) -> None:
        self._update(job_id, status="granted", grantedTool=tool, grantOutcome="re-run started", rerun=rerun_id)

    def fail_grant(self, job_id: str, tool: str, exc: BaseException, outcome: str | None = None) -> None:
        self._update(job_id, status=EXPIRED, grantedTool=tool,
                     grantOutcome=outcome or f"failed: {type(exc).__name__}: {str(exc)[:200]}")

    def grant(self, job_id: str, tool: str, apply_grant, start_rerun) -> tuple[str | None, str | None]:
        """The whole flow: claim, then apply the grant and start the re-run inside a guard that begins right after
        the claim. Returns (re-run job id, None) or (None, refusal). ``apply_grant(tool)`` and
        ``start_rerun(request, tool) -> job id`` are the caller's adapters."""
        request, refusal = self.claim_grant(job_id, tool)
        if refusal:
            return None, refusal
        landed = False
        try:
            apply_grant(tool)
            landed = True
            rerun_id = start_rerun(request, tool)
        except BaseException as e:                      # fail closed on anything, KeyboardInterrupt included
            self.fail_grant(job_id, tool, e, "granted_rerun_failed" if landed else None)
            if not isinstance(e, Exception):
                raise
            return None, EXPIRED
        self.finish_grant(job_id, tool, rerun_id)
        return rerun_id, None

    # -- persistence -----------------------------------------------------------------------------------------------
    def save(self) -> None:
        if not self.path:
            return
        out = {}
        for jid, job in self._jobs.items():
            j = {k: v for k, v in job.items() if k != "from_disk"}
            if j.get("request"):
                j["request"] = j["request"][:PERSISTED_REQUEST_CHARS]
            out[jid] = j
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
        tmp.replace(self.path)

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        for jid, job in json.loads(self.path.read_text(encoding="utf-8")).items():
            if job.get("status") in ("permission_denied", "granting", "running"):
                job["status"] = EXPIRED if job.get("status") != "running" else "interrupted"
            job["from_disk"] = True
            self._jobs[jid] = job
