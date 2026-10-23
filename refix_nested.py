#!/usr/bin/env python3
"""Re-run just the folders affected by the nested-git-repo scanning bug
(resolve_signals now checks one level of subdirectories when the top level
isn't a git repo itself), using the validated 29-folder run as a base and
only overwriting the affected entries."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from declutter import dir_size_kb, human_size, resolve_signals, gemma_verdict, write_report, failed_verdicts, HERE  # noqa: E402

ROOT = Path.home() / "Desktop" / "Project"
AFFECTED = ["Srijan Saree", "OSS", "Trading", "kaggle", "Personal Site", "JobCoachX"]


def audit_one(name: str) -> dict:
    path = ROOT / name
    print(f"re-auditing: {name}", file=sys.stderr)
    kb = dir_size_kb(path)
    size_str = human_size(kb)
    git, graph, deploy = resolve_signals(path)
    v = gemma_verdict(name, size_str, git, graph, deploy)
    print(f"  -> {v['verdict']}: {v['reason']}", file=sys.stderr)
    return {
        "name": name, "path": str(path), "size_str": size_str, "size_kb": kb,
        "git": git, "graph": graph, "deploy_signals": deploy, **v,
    }


def main():
    base = json.loads((HERE / "results.full.json.bak").read_text())
    by_name = {r["name"]: r for r in base}
    for name in AFFECTED:
        by_name[name] = audit_one(name)

    # A raw "Gemma call failed" reason is an exception that leaked into the
    # output, not a verdict. Retry just those folders (up to 2 more times)
    # instead of shipping a report with error text in it.
    for retry in range(2):
        stuck = [n for n in AFFECTED if n in failed_verdicts([by_name[n]])]
        if not stuck:
            break
        print(f"\nretry {retry + 1}/2 for: {', '.join(stuck)}", file=sys.stderr)
        for name in stuck:
            by_name[name] = audit_one(name)

    results = list(by_name.values())
    Path(HERE / "results.json").write_text(json.dumps(results, indent=2))
    write_report(results, HERE / "report.html")
    print(f"\nDone. {len(results)} total folders, {len(AFFECTED)} re-audited with the nested-scan fix.", file=sys.stderr)

    still_failed = failed_verdicts(results)
    if still_failed:
        print(f"\n!! still failed after retries, verify manually before publishing: {', '.join(still_failed)}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
