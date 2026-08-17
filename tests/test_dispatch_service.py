from __future__ import annotations

from typing import Any

import pytest

from app.services import dispatch_service


def test_dispatch_jobs_reports_successes_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_apply_async(**options: Any) -> None:
        job_id = options["args"][0]
        calls.append((job_id, options["task_id"]))
        if job_id == 2:
            raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(dispatch_service.publish_job, "apply_async", fake_apply_async)

    result = dispatch_service.dispatch_jobs((1, 2, 3))

    assert result.dispatched == (1, 3)
    assert result.failed == (2,)
    assert calls == [
        (1, "publish-job-1"),
        (2, "publish-job-2"),
        (3, "publish-job-3"),
    ]
