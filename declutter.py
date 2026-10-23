#!/usr/bin/env python3
"""GraphSloth: a lazy-friendly audit of your abandoned project folders.

Scans a directory of projects, pulls REAL facts per folder (disk size, git
last-commit age, and for git repos a real Graphify structural extraction:
node/edge counts, most-referenced symbol), then asks a local Gemma model to
render a keep/dormant/archive verdict that must cite those facts, never guess.

Never deletes anything. --apply only *moves* ARCHIVE-CANDIDATE folders into
a sibling _Archive directory, and only after per-item confirmation.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:12b-it-q4_K_M"
HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / ".cache"
SKIP_NAMES = {"graphsloth", "_Archive", ".git", "node_modules", "__pycache__", ".DS_Store"}


def human_size(kb: int) -> str:
    mb = kb / 1024
    if mb > 1024:
        return f"{mb / 1024:.1f}G"
    return f"{mb:.0f}M"


def dir_size_kb(path: Path) -> int:
    out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, check=True)
    return int(out.stdout.split()[0])


def git_facts(path: Path) -> dict | None:
    if not (path / ".git").is_dir():
        return None
    try:
        last = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%ct"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return None
    if not last:
        return None
    last_commit = datetime.fromtimestamp(int(last), tz=timezone.utc)
    age_days = (datetime.now(timezone.utc) - last_commit).days
    return {"branch": branch, "last_commit_days_ago": age_days, "dirty": bool(dirty)}


DEPLOY_CONFIG_NAMES = {
    "vercel.json", ".vercel", "render.yaml", "fly.toml", "Procfile",
    "railway.json", "railway.toml", "netlify.toml", "wrangler.toml",
    "docker-compose.yml", "docker-compose.yaml", "app.yaml", "now.json",
}
LIVE_URL_RE = re.compile(
    r"https?://[a-z0-9.-]+\.(?:vercel\.app|railway\.app|onrender\.com|fly\.dev|netlify\.app|herokuapp\.com)[^\s)\"<]*",
    re.I,
)


def deploy_signals(path: Path) -> list[str]:
    """Cheap, local, no-network signals that a folder might be a live deployed
    service rather than an abandoned one. Never proof of liveness, just enough
    to stop the tool from being confident about something it can't verify."""
    signals = []
    try:
        for entry in path.iterdir():
            if entry.name in DEPLOY_CONFIG_NAMES:
                signals.append(entry.name)
    except OSError:
        pass
    readme = path / "README.md"
    if readme.exists():
        try:
            m = LIVE_URL_RE.search(readme.read_text(errors="ignore")[:20000])
            if m:
                signals.append(f"live URL in README: {m.group(0)}")
        except OSError:
            pass
    return signals


def graphify_facts(path: Path, cache_key: str | None = None) -> dict | None:
    out_dir = CACHE_DIR / (cache_key or path.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["graphify", "extract", str(path), "--code-only", "--no-cluster", "--out", str(out_dir)],
            capture_output=True, text=True, timeout=180, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    graph_path = out_dir / "graphify-out" / "graph.json"
    if not graph_path.exists():
        return None
    data = json.loads(graph_path.read_text())
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    top_node = None
    try:
        god_out = subprocess.run(
            ["graphify", "god-nodes", "--top", "1", "--json", "--graph", str(graph_path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        god_data = json.loads(god_out.stdout)
        if god_data:
            top_node = god_data[0].get("label")
    except Exception:
        pass
    return {"nodes": len(nodes), "edges": len(edges), "top_node": top_node}


def resolve_signals(path: Path) -> tuple[dict | None, dict | None, list[str]]:
    """Git facts, Graphify facts, and deploy signals for a folder, checking one
    level of subdirectories when the top level itself isn't a git repo.

    Real bug this exists to fix: several project folders here (Srijan Saree,
    OSS, Trading, kaggle, Personal Site, JobCoachX) are a parent directory
    holding a git repo as a subfolder, not a repo themselves. A top-level-only
    scan sees "not a git repository, no deploy signals" and has zero
    visibility into what's actually inside, including live services."""
    git = git_facts(path)
    deploy = deploy_signals(path)
    if git is not None:
        graph = graphify_facts(path)
        return git, graph, deploy

    try:
        children = [c for c in path.iterdir() if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        children = []

    nested = []  # [(child, git_facts), ...] for every subfolder that's a real git repo
    for child in children:
        cg = git_facts(child)
        if cg is not None:
            nested.append((child, cg))

    nested_deploy = []
    for child in children:
        for sig in deploy_signals(child):
            nested_deploy.append(f"{child.name}/{sig}")
    deploy = deploy + nested_deploy

    if not nested:
        return None, None, deploy

    # Pick whichever nested repo was touched most recently and represent the
    # parent folder by it (fine for a single "code/" subfolder case).
    child, cg = min(nested, key=lambda t: t[1]["last_commit_days_ago"])
    git = {**cg, "nested_in": child.name}
    graph = graphify_facts(child, cache_key=f"{path.name}__{child.name}")
    return git, graph, deploy


def gemma_verdict(name: str, size_str: str, git: dict | None, graph: dict | None, deploy: list[str]) -> dict:
    facts = [f"- disk size: {size_str}"]
    if git:
        state = "has uncommitted changes" if git["dirty"] else "clean working tree"
        where = f" (from nested subfolder '{git['nested_in']}', the top level itself isn't a git repo)" if git.get("nested_in") else ""
        facts.append(f"- last git commit: {git['last_commit_days_ago']} days ago, branch '{git['branch']}', {state}{where}")
    else:
        facts.append("- not a git repository, and no git repo found one level into its subfolders either")
    if graph:
        extra = f", most-referenced symbol: {graph['top_node']}" if graph.get("top_node") else ""
        facts.append(f"- Graphify structural graph: {graph['nodes']} code nodes, {graph['edges']} edges{extra}")
    if deploy:
        facts.append(f"- deploy signals found: {', '.join(deploy)} (this folder may be a live deployed service, "
                      f"not just old code sitting around)")
    else:
        facts.append("- no deploy signals found (no vercel.json, docker-compose.yml, live README link, etc.)")
    facts_block = "\n".join(facts)
    prompt = (
        "You audit a developer's project folders to recommend keep, dormant, or archive. "
        "You are given REAL facts about ONE folder below. Cite only these numbers, never invent any. "
        "A folder with deploy signals might be a stable live service that's rarely touched, not abandoned code; "
        "weigh that honestly instead of treating old commits alone as a reason to archive. "
        "Reply with exactly two lines:\n"
        "VERDICT: one of ACTIVE, DORMANT, ARCHIVE-CANDIDATE\n"
        "REASON: one sentence citing the actual numbers above.\n\n"
        f"Folder: {name}\n{facts_block}\n"
    )
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False}).encode()
    text = ""
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                text = json.loads(resp.read()).get("response", "")
            break
        except Exception as e:  # noqa: BLE001 - genuinely want to retry any transient failure here
            last_err = e
            print(f"    (ollama call failed, attempt {attempt + 1}/3: {e})", file=sys.stderr)
    else:
        return {"verdict": "DORMANT", "reason": f"[Gemma call failed after 3 attempts: {last_err}]",
                "needs_live_check": bool(deploy)}
    verdict = "DORMANT"
    reason = text.strip()
    m = re.search(r"VERDICT:\s*(ACTIVE|DORMANT|ARCHIVE-CANDIDATE)", text, re.I)
    if m:
        verdict = m.group(1).upper()
    r = re.search(r"REASON:\s*(.+)", text, re.I)
    if r:
        reason = r.group(1).strip()

    # Deterministic safety override: never trust the model's wording alone to
    # protect a live service. A folder with real deploy signals never comes
    # back as a clean archive recommendation, no matter what Gemma said.
    needs_live_check = bool(deploy)
    if needs_live_check and verdict == "ARCHIVE-CANDIDATE":
        verdict = "DORMANT"
        reason += " [downgraded from ARCHIVE-CANDIDATE: deploy signals present, verify it isn't live before touching it]"
    return {"verdict": verdict, "reason": reason, "needs_live_check": needs_live_check}


def audit(root: Path) -> list[dict]:
    results = []
    folders = sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name not in SKIP_NAMES and not p.name.startswith(".")
    )
    for i, path in enumerate(folders, 1):
        print(f"[{i}/{len(folders)}] {path.name} ...", file=sys.stderr)
        try:
            kb = dir_size_kb(path)
            size_str = human_size(kb)
            git, graph, deploy = resolve_signals(path)
            verdict = gemma_verdict(path.name, size_str, git, graph, deploy)
            results.append({
                "name": path.name,
                "path": str(path),
                "size_str": size_str,
                "size_kb": kb,
                "git": git,
                "graph": graph,
                "deploy_signals": deploy,
                **verdict,
            })
            print(f"    -> {verdict['verdict']}: {verdict['reason']}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - one bad folder must never kill the whole audit
            print(f"    !! skipped {path.name}: {e}", file=sys.stderr)
            results.append({
                "name": path.name, "path": str(path), "size_str": "?", "size_kb": 0,
                "git": None, "graph": None, "deploy_signals": [],
                "verdict": "ERROR", "reason": f"audit failed: {e}", "needs_live_check": False,
            })
    return results


def write_report(results: list[dict], out_path: Path) -> None:
    order = {"ARCHIVE-CANDIDATE": 0, "DORMANT": 1, "ACTIVE": 2, "ERROR": 3}
    rows = sorted(results, key=lambda r: (order.get(r["verdict"], 1), -r["size_kb"]))
    reclaim_kb = sum(r["size_kb"] for r in rows if r["verdict"] == "ARCHIVE-CANDIDATE")
    reclaim_str = human_size(reclaim_kb)
    check_count = sum(1 for r in rows if r.get("needs_live_check"))

    def card(r: dict, idx: int) -> str:
        cls = {"ARCHIVE-CANDIDATE": "archive", "DORMANT": "dormant", "ACTIVE": "active", "ERROR": "error"}[r["verdict"]]
        graph_line = ""
        if r["graph"]:
            g = r["graph"]
            extra = f" &middot; top symbol <code>{g['top_node']}</code>" if g.get("top_node") else ""
            graph_line = f'<div class="graph">Graphify: {g["nodes"]} nodes / {g["edges"]} edges{extra}</div>'
        git_line = ""
        if r["git"]:
            nested = f" (nested in {r['git']['nested_in']}/)" if r["git"].get("nested_in") else ""
            git_line = f'<div class="git">last commit {r["git"]["last_commit_days_ago"]}d ago on {r["git"]["branch"]}{nested}</div>'
        check_line = ""
        if r.get("needs_live_check"):
            sig = ", ".join(r.get("deploy_signals", []))
            check_line = f'<div class="check">&#9888; deploy signals found ({sig}) &mdash; verify it isn\'t live before touching</div>'
        return f'''
        <article class="card {cls}" style="--i:{idx}">
          <header><h3>{r["name"]}</h3><span class="badge">{r["verdict"]}</span></header>
          <div class="size">{r["size_str"]}</div>
          {git_line}
          {graph_line}
          {check_line}
          <p class="reason">{r["reason"]}</p>
        </article>'''

    html = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GraphSloth audit</title>
<style>
  :root {{ color-scheme: dark; --bg:#0b0d10; --panel:#14171c; --line:#23272e; --fg:#e8eaed; --dim:#8a919c;
    --archive:#e5484d; --dormant:#f5b726; --active:#3fb950; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Inter",sans-serif; padding:40px 20px 80px; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:28px; margin:0 0 4px; }}
  .sub {{ color:var(--dim); margin:0 0 32px; }}
  .stat {{ display:inline-block; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 20px; margin:0 12px 32px 0; }}
  .stat b {{ display:block; font-size:22px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:14px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px;
    border-left:4px solid var(--dim); opacity:0; transform:translateY(8px); animation:in .4s ease forwards; animation-delay:calc(var(--i) * 30ms); }}
  .card.archive {{ border-left-color:var(--archive); }}
  .card.dormant {{ border-left-color:var(--dormant); }}
  .card.active {{ border-left-color:var(--active); }}
  .card.error {{ border-left-color:#555; opacity:.6 !important; }}
  @keyframes in {{ to {{ opacity:1; transform:none; }} }}
  .card header {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
  .card h3 {{ margin:0; font-size:15px; }}
  .badge {{ font-size:10px; letter-spacing:.04em; padding:3px 8px; border-radius:99px; background:#1d2127; color:var(--dim); white-space:nowrap; }}
  .archive .badge {{ color:var(--archive); }}
  .dormant .badge {{ color:var(--dormant); }}
  .active .badge {{ color:var(--active); }}
  .size {{ font-size:22px; font-weight:600; margin-top:6px; }}
  .git, .graph {{ font-size:12px; color:var(--dim); margin-top:4px; }}
  .check {{ font-size:12px; color:var(--dormant); margin-top:8px; padding:6px 8px; background:#2a2008; border-radius:6px; }}
  .reason {{ margin:10px 0 0; font-size:13px; color:#c2c7cf; }}
  code {{ background:#1d2127; padding:1px 5px; border-radius:4px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>GraphSloth audit</h1>
  <p class="sub">Graphify structural facts + local Gemma 4 reasoning, over your real project folders. Nothing moved yet.</p>
  <div class="stat"><b>{len(rows)}</b>folders audited</div>
  <div class="stat"><b>{reclaim_str}</b>reclaimable if you trust the archive candidates</div>
  <div class="stat"><b>{check_count}</b>flagged with deploy signals, verify before touching</div>
  <div class="grid">
    {"".join(card(r, i) for i, r in enumerate(rows))}
  </div>
</div>
</body>
</html>'''
    out_path.write_text(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=str(Path.home() / "Desktop" / "Project"))
    ap.add_argument("--out", default=str(HERE / "report.html"))
    ap.add_argument("--json-out", default=str(HERE / "results.json"))
    ap.add_argument("--apply", action="store_true", help="move ARCHIVE-CANDIDATE folders into _Archive (asks per item)")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    results = audit(root)
    Path(args.json_out).write_text(json.dumps(results, indent=2))
    write_report(results, Path(args.out))
    print(f"\nReport: {args.out}")
    print(f"Results: {args.json_out}")

    if args.apply:
        archive_dir = root / "_Archive"
        candidates = [r for r in results if r["verdict"] == "ARCHIVE-CANDIDATE"]
        for r in candidates:
            ans = input(f"Move '{r['name']}' ({r['size_str']}) to {archive_dir}? [y/N] ").strip().lower()
            if ans == "y":
                archive_dir.mkdir(exist_ok=True)
                shutil.move(r["path"], str(archive_dir / r["name"]))
                print(f"  moved -> {archive_dir / r['name']}")


if __name__ == "__main__":
    main()
