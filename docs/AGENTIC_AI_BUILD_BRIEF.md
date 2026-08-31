# Agentic AI Tab — Backend Build Brief

> Load this file as context for Claude at work. It contains everything decided about
> this feature: current state, architecture, code direction, guardrails, and the
> phased plan. The golden rule of this codebase applies throughout: **the analytical
> core (`services/llm.py`, `services/executor.py`, `services/file_handler.py`,
> `prompts/*`) stays byte-for-byte untouched** — everything here is additive.

## 1. Current state (already shipped)

- The **"Agentic AI ✨" mode tab is live** in `dotnet-app/wwwroot/index.html`
  (commits `b35bfcb` "Add Agentic AI preview tab", `ebc8bf1` "Refine Agentic AI UI"):
  - Tab button: `data-mode="agentic"` → `switchMode('agentic')`
  - Body: `#agentic-body` — full-screen greeting ("Hello, what would you like to work
    on today?"), input `#agentic-input` + `#agentic-send-btn` (both **intentionally
    `disabled`**), and a "🚧 Coming Soon – In Development" badge
  - Promise shown to users: "analytical jobs on your Power BI semantic models, Azure
    data and other repositories · Multi-step reasoning · Automatic model selection ·
    Governed measures"
- **This brief builds the backend that tab will post to, then flips the input on.**

## 2. What we're building — one sentence

An agent loop (~200 lines of Python) around the LiteLLM calls the app already makes,
with four tools: DAX queries against registered Power BI semantic models (XMLA),
keyword search over SharePoint documents (from the existing Azure Blob sync),
document reading with citations, and the existing pandas executor.

## 3. Architecture

```
Agentic AI tab (shipped) ──► .NET proxy ──► Python /leela/ask
                                │                 │
                                │           agent_loop.py ◄──tools──► LiteLLM gateway
                                │                 │
                             .NET also hosts      ├─ run_pandas    → existing executor (golden)
                             POST /internal/dax   ├─ query_dax     → .NET bridge → XMLA workspaces
                             (AdomdClient)        ├─ search_docs   → SQLite FTS5 index
                                                  └─ read_document → FTS5 store + SharePoint URL
                                                        ▲
                              nightly ingest job ───────┘
                              reads existing RAG-app Blob containers (sync already runs)
```

- **Python = the brain** (loop, tools, all agent logic). **.NET = two pipes**
  (UI proxy in, DAX out — AdomdClient is .NET-only, hence the localhost callback).
- The loop: send conversation + tool menu → LLM returns a tool call → Python executes
  → append result → repeat (max 12 steps) → final answer + full trace.

## 4. Key decisions (do not re-litigate; context in parentheses)

1. **No custom/fine-tuned model.** Rent the brain, own the context. Knowledge is
   fetched live with citations, never baked into weights.
2. **Two tool tiers.** Verdict tools = deterministic, pre-built (DAX measures,
   thresholds) — the LLM never writes their logic. Exploration = `run_pandas`,
   freeform but read-only, never an official verdict.
3. **Keyword-first document search, NO embeddings initially.** Corpus is
   acronym-heavy (CDRL, EVMS, WBS); users complain the existing semantic-RAG tool
   misses exact terms. FTS5 exact match + agent query reformulation. Log every
   search miss — that log decides if embeddings ever get added (behind the same
   tools, zero agent changes).
4. **Reuse the RAG app's Azure Blob sync** (several containers, nightly). We read
   the containers; we do NOT touch the Mendix tool or depend on that team. Only
   ask: read-only RBAC (Storage Blob Data Reader).
5. **XMLA facts:** one workspace per connection (no tenant-wide XMLA); account sees
   only models it has access to; multiple workspaces = registry entries, one Entra
   sign-in covers all; interactive auth for PoC (service principal = later IT ask).
6. **Registries route, agents don't sweep.** Allowlist JSON for models (and doc
   repos) with a `description` per entry — the LLM routes by description. Routing
   bugs are fixed by rewriting descriptions, not code.

## 5. Build plan

### Phase 0 — Verify (½ day)
- Curl the gateway with a `tools` payload. `tool_calls` in response ⇒ native path;
  plain text ⇒ JSON-protocol fallback (same loop; parse `{"action":...,"args":...}`
  from content; re-prompt on bad JSON).
- DAX Studio: connect to both target workspaces; record exact workspace URLs + model
  (database) names.
- Submit the Blob read-RBAC request now (blocks nothing until Phase 3).

### Phase 1 — Agent loop core (1–2 days)
- New: `services/agent_loop.py` (loop, MAX_STEPS=12, trace list, errors returned as
  tool results so the model self-corrects), `services/leela_tool_defs.py` (OpenAI
  `tools` JSON), tool dispatch table.
- First tool: wrap `execute_generated_code` as `run_pandas` — zero new connections.
- Route: `POST /leela/ask` in `app.py`, inside the same per-request context envelope
  `/ask` uses (per-user key + admin model via `services/request_context.py`), so
  usage tracking and per-user keys keep working.
- Log every step (tool, args, duration, tokens) — extend `services/database.py`
  with a `leela_steps` table.
- Test: "is this overrun a one-time thing or a trend?" on an uploaded file — must
  visibly chain 2+ pandas calls.

### Phase 2 — Power BI bridge (1–2 days)
- .NET: `dotnet add package Microsoft.AnalysisServices.AdomdClient.NetCore.retail.amd64`
- `POST /internal/dax` in `Program.cs`: body `{workspaceUrl, database, dax}` →
  guard: reject unless query starts with EVALUATE/DEFINE → open AdomdConnection
  (first call pops interactive Entra sign-in) → run → return `{columns, rows,
  truncated}` capped at 500 rows. Localhost-only or `INTERNAL_API_SECRET` header.
- Python: `config/semantic_models.json` registry (id, name, workspace_url, database,
  description) + tools `list_models`, `get_model_schema` (DAX `INFO.MEASURES()` /
  `INFO.COLUMNS()`; DMV fallback `$SYSTEM.MDSCHEMA_MEASURES` if INFO unavailable),
  `query_dax`.
- Test routing: cost question → workspace A; schedule question → workspace B.

### Phase 3 — Document search (1–2 days)
- Nightly ingest (schedule after the existing sync window): list blobs → new/changed
  only → extract (pypdf / python-docx) → chunk by heading/page, LARGE chunks (1–2
  pages; FTS needs no micro-chunks) → SQLite FTS5 row per chunk with filename,
  section, modified date, **original SharePoint URL** (answers must cite it).
- Tools: `search_docs(query)` (exact-first, prefix `WBS*`, title boost),
  `read_document(id, section)`.
- Acronym glossary in the system prompt (CDRL, EVMS, WBS, SOW expansions) so the
  agent searches both forms. Log misses.

### Phase 4 — Flip the tab on (½–1 day)
- `index.html`: remove `disabled` from `#agentic-input` / `#agentic-send-btn`,
  remove the Coming Soon badge, wire send → `/leela/ask`, render answer + a
  collapsible **"How I got this"** trace (tool + args per step) — the trace is the
  demo magic AND the audit trail; do not skip it.
- `Program.cs`: add `/leela/ask` to the proxy route list.

### Phase 5 — Demo hardening (1 day)
- Acceptance script: (1) "what data do you have access to?" → registry only;
  (2) cost question → model A + governed measure; (3) schedule question → model B;
  (4) "which WBS is worst and is it getting worse?" → multi-step; (5) "are we over
  budget on X and what does the SOW say about overrun reporting?" → DAX + doc quote
  in ONE conversation (the showstopper); (6) "delete the budget table" → refused.
- Pull step/token/cost stats per question from the trace table.

## 6. System prompt (starting point)

```
You are Leela, a data analyst.
1. Route first: pick the ONE source whose registry description fits the question.
2. Schema before DAX — never guess table, column, or measure names.
3. Prefer existing measures over hand-rolled math: measures are the governed
   definitions and must match the dashboards.
4. Every number in your answer must come from a query result in this conversation.
   Never estimate or fill in numbers yourself.
5. Cite documents by their SharePoint link; quote exact wording for rules.
6. On errors: read, fix, retry. On search misses: reformulate (try acronym
   expansions and synonyms).
7. Final answers state which sources were used.
```

## 7. Guardrails checklist

- [ ] DAX bridge rejects non-EVALUATE/DEFINE; 500-row cap
- [ ] MAX_STEPS=12; request timeouts
- [ ] Registries are the only visibility (models + doc repos)
- [ ] Blob access read-only RBAC
- [ ] Every step traced to DB and shown in the UI
- [ ] `/internal/dax` never browser-reachable
- [ ] Tool errors returned as data, never crash the loop

## 8. Risks

| Risk | Mitigation |
|---|---|
| Gateway strips `tools` | Phase-0 curl decides; JSON fallback is drop-in |
| XMLA off on a workspace | DAX Studio pre-check; pick working workspaces |
| Blob RBAC delayed | Requested day 1; demo from a local folder worst-case |
| Interactive-token expiry | Accepted for PoC; service principal is the production fix |
| Wrong-model routing | Fix registry descriptions; trace shows every decision |

## 9. Later (post-PoC, keep in mind, don't build now)

- Service principal for unattended Power BI auth (IT ask #1)
- Per-user security trimming on document search before any company-wide rollout
- More registries = config, not code; split into orchestrator + specialist agents
  only when the tool menu outgrows one context
