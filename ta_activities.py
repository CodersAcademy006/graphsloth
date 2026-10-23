"""The activity implementation. Only the worker process imports this file -
the workflow module never does, so declutter.py's real subprocess/urllib I/O
never enters Temporal's deterministic workflow sandbox."""
import sys
from pathlib import Path

from temporalio import activity

sys.path.insert(0, str(Path(__file__).resolve().parent))
from declutter import dir_size_kb, human_size, resolve_signals, gemma_verdict  # noqa: E402

ROOT = Path.home() / "Desktop" / "Project"


@activity.defn(name="audit_folder")
def audit_folder(name: str) -> dict:
    """One folder, fully audited. This is the durable unit of work: once this
    activity returns, Temporal has it in the workflow's event history, and a
    worker restart will never redo it."""
    path = ROOT / name
    activity.logger.info(f"auditing {name}")
    kb = dir_size_kb(path)
    size_str = human_size(kb)
    git, graph, deploy = resolve_signals(path)
    verdict = gemma_verdict(name, size_str, git, graph, deploy)
    return {
        "name": name, "path": str(path), "size_str": size_str, "size_kb": kb,
        "git": git, "graph": graph, "deploy_signals": deploy, **verdict,
    }
