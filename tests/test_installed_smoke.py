"""The release smoke must await durable work and reject partial or stalled checks."""

import pytest

from scripts.check_installed import wait_for_mcp_job


async def test_installed_smoke_waits_for_durable_job_completion():
    states = iter(["queued", "running", "completed"])
    calls = []

    async def call(name, arguments):
        calls.append((name, arguments))
        state = next(states)
        return {"job_id": "job-smoke", "state": state, "outcome": "complete"}

    result = await wait_for_mcp_job(call, "job-smoke")
    assert result["state"] == "completed"
    assert calls == [("djlib_job", {"job_id": "job-smoke"})] * 3


@pytest.mark.parametrize(
    ("state", "outcome"),
    [("completed", "completed_with_gaps"), ("failed", None), ("paused", None)],
)
async def test_installed_smoke_rejects_terminal_partial_or_failed_jobs(state, outcome):
    async def call(name, arguments):
        return {"job_id": "job-smoke", "state": state, "outcome": outcome}

    with pytest.raises(AssertionError, match=state):
        await wait_for_mcp_job(call, "job-smoke")


async def test_installed_smoke_timeout_retains_job_context():
    async def call(name, arguments):
        return {"job_id": "job-smoke", "state": "running", "outcome": None}

    with pytest.raises(AssertionError, match="timed_out.*job-smoke"):
        await wait_for_mcp_job(call, "job-smoke", timeout=0)
