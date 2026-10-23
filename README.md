# GraphSloth

A lazy-friendly audit of your abandoned project folders. Built for the Hacktoberfest Open-Source AI Challenge: Week 3 ("Slow and Steady").

Most developers have a `~/Desktop/Project` folder full of things they built, half-finished, and never looked at again. Deciding what's safe to archive is tedious enough that nobody does it. GraphSloth does it for you, overnight, and cites real evidence instead of guessing.

## How it works

For each top-level folder:

1. **Disk size** — `du -sk`.
2. **Git facts** — last commit age, branch, dirty state (if it's a git repo).
3. **Graphify structural extraction** — real tree-sitter AST parsing via [Graphify](https://github.com/Graphify-Labs/graphify), zero LLM cost: node count, edge count, most-referenced symbol.
4. **Gemma 4 verdict** — a local model (`gemma4:12b-it-q4_K_M` via Ollama) reads those real facts and returns `ACTIVE` / `DORMANT` / `ARCHIVE-CANDIDATE` plus a one-sentence reason that must cite the actual numbers. No invented facts, no vibes.

Output is `report.html`: a single static page, sorted by reclaimable size.

## Durability, via Temporal

The first plain-Python run (`declutter.py`) got killed mid-way through 29 folders by an OS memory-pressure reaper (Gemma 4 is an 8GB-resident model, the machine ran low on RAM in the background), at folder 28 of 29, and recovering meant hand-parsing stderr logs back into results.

`temporal_audit.py` runs the exact same per-folder audit as a Temporal workflow instead, with each folder's result as a durable activity:

```bash
python3 temporal_audit.py worker              # one terminal
python3 temporal_audit.py start                # another terminal, all folders
```

Proven live, not just claimed: started a 5-folder run, let 2 complete, `kill -9`'d the worker process mid-activity, confirmed via `temporal workflow describe` that the workflow was still alive on the server with its 2 completed results intact, restarted the worker, and watched it resume from folder 3 rather than starting over. `ta_workflow.py` holds only the deterministic workflow definition (Temporal's sandbox forbids filesystem/subprocess access there); `ta_activities.py` holds the actual I/O, imported only by the worker process.

Nothing is deleted or moved by default. `--apply` only *moves* archive candidates into a sibling `_Archive/` folder, and asks for a yes/no per folder before touching anything.

## Known limitation: quiet isn't the same as abandoned

The verdict leans heavily on git commit age. A side project untouched for months is genuinely dormant, but a stable, deployed service you rarely need to touch (a live backend, a production site) can look just as "stale" by that same signal, even while it's serving real traffic right now.

GraphSloth checks for cheap, local, no-network signals of a live deployment (`vercel.json`, `.vercel`, `docker-compose.yml`, `render.yaml`, `fly.toml`, a live URL in the README, etc.) before letting Gemma weigh in, and the result is a hard, code-level rule, not a hope that the model remembers: **a folder with any deploy signal can never come back as `ARCHIVE-CANDIDATE`**, it's capped at `DORMANT` and flagged with a visible "verify before touching" warning on its card, regardless of what the model's text says. This isn't proof of liveness, a repo can carry deploy config for a service that's long dead (one of mine does), so the flag means "check it yourself," not "it's definitely fine."

Nothing is ever auto-archived in bulk. `--apply` asks per folder, one at a time, and every `ARCHIVE-CANDIDATE` deserves a real look before you say yes, not a rubber stamp.

## Why open matters here

- **Zero cost.** Graphify's extraction is local tree-sitter parsing, no API key. Gemma 4 runs locally via Ollama. No cloud bill for auditing dozens of gigabytes.
- **Privacy.** Several of the audited folders are private client work. Nothing about their contents leaves the laptop.
- **Real evidence over vibes.** Most "clean up your projects" tools work off `mtime` alone. GraphSloth grounds the verdict in a real structural graph of the code, the same pattern that worked for [OneGraph](https://github.com/CodersAcademy006/onegraph) (my MLH Hack Day project the week before this one): an LLM reasoning over real graph facts beats an LLM guessing.

## Run it

```bash
python3 declutter.py                 # defaults to ~/Desktop/Project, writes report.html
python3 declutter.py --apply         # also offers to move ARCHIVE-CANDIDATE folders, asks per item
```

Requires `ollama` running locally with a Gemma model pulled, and the [`graphify`](https://github.com/Graphify-Labs/graphify) CLI on PATH. For the Temporal variant, also `pip install -r requirements.txt` in a venv and a local Temporal server (`temporal server start-dev`).

## Prize categories

Best Use of Gemma, Best Use of Temporal. Graphify is not an official Week 3 partner category, it's just the real open-source tool making the verdicts trustworthy instead of invented.
