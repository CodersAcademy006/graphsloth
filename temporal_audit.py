#!/usr/bin/env python3
"""GraphSloth's audit loop as a durable Temporal workflow.

Why this exists: the first plain-Python run of declutter.py got killed mid-way
by an OS memory-pressure reaper, at folder 28 of 29, and recovering meant
hand-parsing stderr text back into results. A Temporal workflow checkpoints
each folder's result as it completes; if the worker process dies, a fresh
worker resumes the SAME workflow run from its event history instead of
redoing finished work or losing it.

Usage:
    python3 temporal_audit.py worker                  # run in one terminal
    python3 temporal_audit.py start NAME1 NAME2 ...   # kick off a run (default: all folders)
"""
import asyncio
import concurrent.futures
import json
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from ta_activities import audit_folder, ROOT
from ta_workflow import AuditWorkflow, TASK_QUEUE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from declutter import write_report, HERE  # noqa: E402


async def run_worker():
    client = await Client.connect("localhost:7233")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[AuditWorkflow],
            activities=[audit_folder],
            activity_executor=pool,
        )
        print(f"Worker started on task queue '{TASK_QUEUE}'. Ctrl+C to stop.", file=sys.stderr)
        await worker.run()


async def run_start(names: list[str], workflow_id: str):
    client = await Client.connect("localhost:7233")
    print(f"Starting workflow {workflow_id} for {len(names)} folders ...", file=sys.stderr)
    results = await client.execute_workflow(
        AuditWorkflow.run,
        names,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    Path(HERE / "results.json").write_text(json.dumps(results, indent=2))
    write_report(results, HERE / "report.html")
    print(f"Done. {len(results)} folders. Report: {HERE / 'report.html'}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "worker":
        asyncio.run(run_worker())
    elif cmd == "start":
        rest = sys.argv[2:]
        workflow_id = "graphsloth-audit-run"
        if rest and rest[0] == "--id":
            workflow_id = rest[1]
            rest = rest[2:]
        names = rest or sorted(
            p.name for p in ROOT.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in {"_Archive", "graphsloth"}
        )
        asyncio.run(run_start(names, workflow_id))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
