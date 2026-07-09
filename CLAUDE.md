# CLAUDE.md — Project context & handoff (RAM Pilot, v2 features)

> Auto-loaded by Claude Code. Complete handoff so a fresh Claude instance can
> understand this project and guide the user (Thiago). Read it fully before acting.

## What this project is

**Chat with Data — RAM Pilot.** An AI app that answers plain-English questions
about uploaded data files: an LLM (1) generates pandas code, it executes, then
(2) turns the result into a written answer + optional chart. Every number is
computed by executed pandas code, not estimated. Scope: **Standard** and
**Variance** modes.

This repo is a **full copy of `chat-with-data-RAM`** (Option 1 — single server,
everything in RAM, ONE Python process) **plus the v2 user-experience features**
below. The base repo remains the plain edition; `chat-with-data-dotnet` remains
the scale-out option (SQL Server + file share + load balancer).

## v2 features added in this pilot (all additive)

- **Word report export** (`/export/conversation`, `services/report_export.py`,
  python-docx, GA logo) — exports the conversation faithfully: questions,
  answers, chart PNGs captured from the live canvases, optional Query &
  Calculations appendix. This deliberately REPLACES saved history — the user
  does not want server-side history/retention. Clear Chat offers export first.
- **Pin dashboard** — Chat/Dashboard view toggle in the header; pinned answer
  snapshots (session-scoped, client-side only).
- **Tab-switch fix** — per-tab transcripts kept on screen (`transcripts` in
  index.html); uploads/Clear clear ONLY their own tab (`clear_history(mode)`).
- **Upload insights** (`services/insights.py`, PURE pandas, zero LLM): starter
  question chips, data profile card (sidebar, below the schema pills — keep the
  schema pills, the user likes them), anomaly "did you notice?" nudge.
- **One sentence / More detail** (`/ask/refine`) — re-runs ONLY Call 2 on the
  stored last result (numbers cannot change); enabled on the latest answer only.
- **Chart type switcher** (bar/line/pie, client-side re-render), **copy answer**,
  **question history dropdown** (in-session), **dark mode** (localStorage +
  dark chart palette), **Variance Baseline/Current labels** with dates
  auto-detected from filenames.
- **Cancelled by the user — do NOT add:** voice/audio input (data-egress
  concern), scheduling, saved conversation history, share links.

## The defining design choice: stateful, in RAM, ONE process

- All session state (uploaded DataFrame(s), schema, conversation history, last
  result) lives in the **Python process memory** (`STORAGE_BACKEND=memory`), keyed
  by the session id the .NET layer sends in `X-Session-Id`.
- Follow-up questions reuse the in-RAM data — no reload from disk/DB → fast.
- **Runs as ONE process** (waitress or gunicorn `--workers 1`); concurrency comes
  from **threads** (workload is I/O-bound, waiting on the LLM). **Never run
  multiple workers/processes here** — separate memory would split sessions.
- **A restart clears all sessions** (nothing persisted). Acceptable for a small
  base; users re-upload.

## Architecture
```
Browser ──► .NET front end (ASP.NET Core)  ──HTTP + X-Session-Id──►  Python (Flask, ONE process, state in RAM)  ──►  LiteLLM gateway
            UI · session cookie · proxy                              two-call pipeline (pandas)
```
- `dotnet-app/` — ASP.NET Core: UI, session cookie `cwd_sid`, **proxies** all data
  calls to Python. No analytical logic. Runs on **:5080**.
- `python-service/` — Flask REST API (`app.py`). Runs on **:8000**.

### ⚠️ Golden rule — DO NOT change the analytical core
Byte-for-byte identical to the validated prototype; must stay that way:
`services/llm.py`, `services/executor.py`, `services/file_handler.py`,
`prompts/standard.py`, `prompts/variance.py`. Data manipulation is pandas, exactly
as the prototype. Infrastructure (serving, config) is fair game; the core is not.

## What differs from the base `chat-with-data-RAM` repo (pilot changes)
1. `python-service/services/insights.py` — **NEW**: profile / anomalies /
   starter questions (pure pandas).
2. `python-service/services/report_export.py` — **NEW**: conversation → Word
   (python-docx, GA logo from `static/images/ga_logo.png`).
3. `python-service/app.py` — upload responses now include insights; uploads
   clear only their own mode's history; `_run_pipeline` also returns
   result_str/metadata; new `/api/ask/refine` and `/api/export/conversation`.
4. `python-service/requirements.txt` — added `python-docx`.
5. `dotnet-app/Program.cs` — proxy routes for `/ask/refine` and
   `/export/conversation`.
6. `dotnet-app/wwwroot/index.html` — all v2 UI (per-tab transcripts, chips,
   profile card, nudges, actions row, chart switcher, dashboard, dark mode,
   history dropdown, Export Report).

The RAM edition's own diffs vs `chat-with-data-dotnet` (Dockerfile workers=1,
memory backend, `serve.py` waitress entry) all carry over unchanged.
`storage.py` still contains the `disk`/`sqlserver` backends but they are **not
used** here (memory only).

## How a request flows
1. Browser calls .NET (`/upload/standard`, `/ask`, `/export/...`).
2. .NET resolves the session cookie, forwards to Python with `X-Session-Id`.
3. Python: Call 1 generate code (temp 0) → execute in one namespace → Call 2
   explain + optional chart (temp 0.3). State stays in RAM for follow-ups.
4. .NET relays the response (or an Excel/PNG file) back.

## Features (all implemented & verified)
Standard + Variance modes, Chart.js charts, Export to Excel
(`/export/last_result`), Export raw query data multi-sheet
(`/export/debug_result`), Export visuals (chart → PNG), Show Query and
Calculations debug panel, multi-sheet upload, per-user in-RAM sessions.

## Run locally
Prereqs: .NET SDK 8.0, Python 3.10+. Live answers need the GA network/VPN; else use
the mock.
- `./run.sh` (macOS/Linux) or `.\run.ps1` (Windows) → open http://localhost:5080.
- Docker: `docker compose up --build` (single container, memory).
- Offline: `python python-service/mock_llm.py` (:9000), then run the service with
  `LITELLM_API_BASE=http://localhost:9000 LITELLM_API_KEY=mock`.

## Run on the server (single box)
```
cd python-service && pip install -r requirements.txt && python serve.py
```
`serve.py` = waitress, one process, `WAITRESS_THREADS` (default 16), binds
`127.0.0.1:8000`. .NET (IIS) points `PythonService:BaseUrl` at `http://localhost:8000`.
On Windows, register `serve.py` as a service (NSSM) for auto-start/restart.

## Sizing (small base)
I/O-bound (each query waits on the LLM), so a single process with ~16 threads
handles many concurrent users. **RAM is the driver** — each active user's data sits
in memory; with files up to ~50 MB, budget a few hundred MB per concurrent active
user plus headroom. A single ~4 vCPU / 16–32 GB box is a sensible start; scale
**up** (bigger box) if needed. See the architecture doc for details + cost estimate.

## Gotchas
- **ONE process only.** Never raise gunicorn `--workers` above 1 or run multiple
  instances here — it would split in-RAM sessions. Threads are fine.
- **Restart = sessions cleared.** No persistence by design.
- **Port 5000 on macOS** is AirPlay — .NET uses **5080**.
- **Windows:** use `serve.py` (waitress); gunicorn is Linux-only.
- **Live answers need the GA gateway/VPN**; off-network use `mock_llm.py` (test only).
- **Don't change the analytical core / prompts** (see Golden rule).

## To scale beyond one server
Switch to the `chat-with-data-dotnet` repo (same app; adds SQL Server + file share +
multiple stateless instances behind a load balancer). Do not try to multi-instance
this in-RAM edition.
