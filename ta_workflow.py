"""The workflow definition only. Kept free of any non-deterministic imports
(subprocess, urllib, filesystem) so Temporal's workflow sandbox can load it.
The activity is referenced by name, not imported directly - its real
implementation (and declutter.py's I/O) lives in ta_activities.py, which only
the worker process imports."""
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

TASK_QUEUE = "graphsloth-audit"


@workflow.defn
class AuditWorkflow:
    @workflow.run
    async def run(self, names: list[str]) -> list[dict]:
        results = []
        for name in names:
            result = await workflow.execute_activity(
                "audit_folder",
                name,
                start_to_close_timeout=timedelta(minutes=6),
                retry_policy=RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0),
            )
            results.append(result)
        return results
