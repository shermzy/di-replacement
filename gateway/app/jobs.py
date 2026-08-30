"""In-memory async job store (single-process gateway)."""
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Job:
    result_id: str
    model_id: str
    status: str = "running"  # running | succeeded | failed
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    result: Optional[dict] = None
    error: Optional[dict] = None

    def public(self) -> dict:
        out = {
            "status": self.status,
            "createdDateTime": _iso(self.created),
            "lastUpdatedDateTime": _iso(self.updated),
        }
        if self.status == "succeeded":
            out["analyzeResult"] = self.result
        if self.status == "failed" and self.error:
            out["error"] = self.error
        return out


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class JobStore:
    TTL_SECONDS = 24 * 3600

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def new(self, model_id: str) -> Job:
        job = Job(result_id=str(uuid.uuid4()), model_id=model_id)
        self._jobs[job.result_id] = job
        self._gc()
        return job

    def get(self, result_id: str) -> Optional[Job]:
        return self._jobs.get(result_id)

    def _gc(self) -> None:
        cutoff = time.time() - self.TTL_SECONDS
        for rid in [r for r, j in self._jobs.items() if j.created < cutoff]:
            del self._jobs[rid]
