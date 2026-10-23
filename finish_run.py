#!/usr/bin/env python3
"""One-off recovery: reconstruct the 27 completed results from audit.log
(no re-running Gemma on folders already verdicted), then run gemma_verdict
fresh only for the folders the killed run never finished."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from declutter import (  # noqa: E402
    dir_size_kb, human_size, git_facts, graphify_facts, deploy_signals,
    gemma_verdict, write_report, HERE,
)

ROOT = Path.home() / "Desktop" / "Project"
LOG = HERE / "audit.log"

line_re = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+?)\s+\.\.\.$")
verdict_re = re.compile(r"^\s*->\s*(ACTIVE|DORMANT|ARCHIVE-CANDIDATE|ERROR):\s*(.+)$")


def parse_log(path: Path):
    entries = {}
    pending_name = None
    for raw in path.read_text().splitlines():
        m = line_re.match(raw)
        if m:
            pending_name = m.group(3)
            continue
        v = verdict_re.match(raw)
        if v and pending_name:
            entries[pending_name] = {"verdict": v.group(1), "reason": v.group(2)}
            pending_name = None
    return entries


def rebuild(name: str, verdict: str, reason: str) -> dict:
    path = ROOT / name
    kb = dir_size_kb(path)
    size_str = human_size(kb)
    git = git_facts(path)
    graph = graphify_facts(path) if git else None
    deploy = deploy_signals(path)
    return {
        "name": name, "path": str(path), "size_str": size_str, "size_kb": kb,
        "git": git, "graph": graph, "deploy_signals": deploy,
        "verdict": verdict, "reason": reason, "needs_live_check": bool(deploy),
    }


def fresh(name: str) -> dict:
    path = ROOT / name
    print(f"running fresh: {name}", file=sys.stderr)
    kb = dir_size_kb(path)
    size_str = human_size(kb)
    git = git_facts(path)
    graph = graphify_facts(path) if git else None
    deploy = deploy_signals(path)
    v = gemma_verdict(name, size_str, git, graph, deploy)
    print(f"  -> {v['verdict']}: {v['reason']}", file=sys.stderr)
    return {
        "name": name, "path": str(path), "size_str": size_str, "size_kb": kb,
        "git": git, "graph": graph, "deploy_signals": deploy, **v,
    }


def main():
    recovered_names = sorted(
        p.name for p in ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in {"_Archive", "graphsloth"}
    )
    log_entries = parse_log(LOG)
    results = []
    missing = []
    for name in recovered_names:
        if name in log_entries:
            e = log_entries[name]
            print(f"recovered from log: {name} -> {e['verdict']}", file=sys.stderr)
            results.append(rebuild(name, e["verdict"], e["reason"]))
        else:
            missing.append(name)

    print(f"\n{len(results)} recovered from log, {len(missing)} to run fresh: {missing}\n", file=sys.stderr)

    for name in missing:
        results.append(fresh(name))

    Path(HERE / "results.json").write_text(json.dumps(results, indent=2))
    write_report(results, HERE / "report.html")
    print(f"\nDone. {len(results)} total folders.", file=sys.stderr)


if __name__ == "__main__":
    main()
