# Rollout batch — per-user API keys, usage tracking, dashboards, model selection

Implemented August 2026 on the RAM Pilot. Everything in this batch is
**additive**: the analytical core (`services/llm.py`, `services/executor.py`,
`services/file_handler.py`, `prompts/standard.py`, `prompts/variance.py`)
remains **byte-for-byte identical** to the validated prototype — verified after
implementation.

## What was added

| Feature | What it does |
|---|---|
| Per-user API keys | First visit shows a **blocking onboarding modal** with step-by-step instructions (and the request-portal link) to get a personal LiteLLM key. The key is validated against the gateway, then stored **Fernet-encrypted** in SQLite. Every question runs on the asking user's own key. |
| Usage tracking | Every gateway call is logged with exact token counts from the LiteLLM response: user, session, question (truncated to 500 chars), call type (`call1_codegen` / `call2_answer` / `refine_answer`), model, tokens, and cost from the pricing table. |
| My usage (📊) | Per-user dashboard: questions / tokens / cost / avg-per-question for the last 30 days, a tokens-per-day chart, and recent question history. |
| Settings (⚙) | View the stored key (masked, e.g. `sk-****2345`) and replace it. |
| Admin dashboard (🛡) | Admins only: org-wide totals, active users, top users by cost, and the model selector. Admins are a comma-separated `ADMIN_USERS` list in `.env`. |
| Model selection | Admin picks the model for **all users** from the gateway's live `/v1/models` list. Applies immediately to every subsequent question; every change is audit-logged (who, when, previous model, reason). |

## How it preserves the golden rule (important for future work)

- `llm.py` reads `Config.LITELLM_API_KEY` / `Config.LLM_MODEL` on every call.
  `config.py` now serves those two values **dynamically** from a per-request
  context (`services/request_context.py`) — the user's decrypted key and the
  admin-selected model — falling back to `.env` values otherwise. Thread-safe
  via contextvars; set/reset around `/ask` and `/ask/refine` in `app.py`.
- Token counts are captured by `services/usage_capture.py`, a thin proxy
  installed **around** the network call `llm.py` makes. It copies the `usage`
  block off each gateway response as it passes through and returns the response
  unchanged. If capture fails, the call proceeds normally.
- Net effect: per-user keys, per-request model, and exact token tracking with
  **zero edits** to the golden files.

## New files

```
python-service/services/request_context.py   per-request contextvars (key, model, capture)
python-service/services/usage_capture.py     token capture shim + DB flush
python-service/services/database.py          SQLite schema + all queries
python-service/services/crypto.py            Fernet encrypt/decrypt/mask (lazy init)
python-service/services/admin_auth.py        ADMIN_USERS check (domain-form tolerant)
python-service/services/model_manager.py     gateway model list + active model
python-service/services/pricing.py           $/1M-token table (edit to match gateway billing)
python-service/chatdata.db                   created at runtime — GITIGNORED, never commit
```

Modified: `config.py` (dynamic key/model + new settings), `app.py` (new
endpoints + the per-request envelope around ask/refine), `Program.cs` (identity,
query-string forwarding, 15 proxy routes, optional secret header),
`index.html` (modals + header buttons + `needs_key` hook), `mock_llm.py`
(test-only: `/v1/models` + fake token usage), requirements (+`cryptography`).

## Configuration reference (python-service/.env)

```
ENCRYPTION_KEY=       REQUIRED for key storage. Generate once:
                      python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                      Losing/changing it = stored keys unreadable; users simply re-enter.
ADMIN_USERS=          Comma-separated usernames; DOMAIN\name and bare name both match.
INTERNAL_API_SECRET=  Optional shared secret (see security note below).
DB_PATH=              Optional; defaults to python-service/chatdata.db.
```

.NET (`appsettings.*.json`):
```
Auth:DevUsername             Optional. When Windows Auth is off, overrides the identity
                             (useful to simulate another user / a non-admin). Default:
                             the machine's logged-in username.
PythonService:InternalSecret Optional. Pair with INTERNAL_API_SECRET.
```

## How identity works (no new auth system)

`X-User` is attached by .NET to every proxied request:
1. **Windows Authentication on** (server build) → the real Windows username.
2. **Auth off** (local dev) → `Auth:DevUsername` if set, else the machine's
   logged-in username.

So the feature set works today on a dev laptop and upgrades itself to real
per-person identity the moment the server team enables Windows Auth. No code
changes needed then.

## ⚠️ SECURITY REMINDERS (address before/at rollout)

1. **Turn on the shared secret while Python runs on a separate machine.**
   Python trusts the `X-User` header. Until the service sits on the same box as
   .NET (bound to localhost), anyone on the network who can reach port 8000
   could claim any username and use that user's stored key. Mitigation shipped
   in this batch — set the SAME value in both places and restart:
   - `INTERNAL_API_SECRET=<value>` in `python-service/.env`
   - `PythonService:InternalSecret=<value>` in the .NET appsettings
   Once .NET and Python are co-located and Python binds to `127.0.0.1`, this
   becomes defense-in-depth rather than a necessity.
2. **New data at rest.** `chatdata.db` now holds encrypted user keys and usage
   logs (usernames, timestamps, question text truncated to 500 chars, kept
   indefinitely). Protect the file with normal server ACLs; keep
   `ENCRYPTION_KEY` out of source control. The egress posture is unchanged —
   the only outbound destination is still the LiteLLM gateway (`/v1/models` and
   `/v1/chat/completions`).
3. **Admin list requires a service restart** to pick up `.env` changes.

## Verified in this batch (mock gateway, full browser run)

- Blocking onboarding for a new user; returning user skips it; invalid key
  rejected by gateway validation; key stored encrypted (`gAAAA…` in DB).
- Ask + refine run on the user's key; usage rows carry exact tokens and costs;
  1 question = 1 `RequestID` across its calls (retries don't double-count).
- Admin: 403 for non-admins; model changed sonnet→gpt-4o via UI and the very
  next question was billed/logged under gpt-4o; change audit-logged.
- Internal secret: requests without the header rejected, with it accepted,
  `/health` unaffected.
- Regression: upload, chips, profile, anomalies, charts, refine, Excel export,
  Word report export, dark mode, tab persistence — all intact; zero console
  errors; golden files byte-identical.


---

# Rollout batch 2 — Data Experts (Aug 2026)

The tabular twin of the RAG app's "experts": an admin publishes a governed
Excel/CSV; an authorized population questions it in the Standard tab — no
uploading, always the current file. Fully additive; golden core re-verified
byte-identical after implementation.

## How it works
- **Admin console → Data experts**: create an expert (name, description, data
  file up to the existing 50 MB limit, .xlsx/.csv, sheet for multi-sheet
  workbooks), choose access, and optionally write up to **6 recommended
  questions** (one per line) — these become the user's suggestion chips,
  replacing the auto-generated ones. Edit / Replace file / Disable per row.
  Replacing the file is how data gets refreshed.
- **Users**: a "Data Experts" card appears in the Standard sidebar ONLY for
  users allowed at least one expert. Pick → "Load this expert" → identical
  experience to having uploaded the file (title shows the expert's name, plus a
  "data as of <file timestamp>" trust line). Users with no access see nothing.
- **Under the hood**: loading feeds the stored file through the SAME code path
  as an upload — pipeline, insights, usage tracking, and exports untouched.
  Files live in python-service/datasets/ (gitignored); metadata in SQLite
  (DataExperts table).

## Access control (enforced server-side on list AND load)
- Radio per expert: **"Only specific people" (default)** or "All app users".
- Restricted = username list (paste-friendly: commas or newlines, DOMAIN\
  prefixes tolerated, case-insensitive) **OR AD groups**.
- **AD groups are fully wired now**: Windows sign-in hands .NET each visitor's
  group badge; .NET checks it against only the group names experts reference
  (list cached from /api/experts/groups_in_use, 60 s) and forwards matches as
  X-User-Groups. Activates automatically wherever Windows Auth is on.
- **Testing groups before real auth**: set Auth:DevGroups in the .NET settings
  (e.g. "AR-Users") to simulate membership — ignored once a user is genuinely
  authenticated. X-User-Groups rides the same trust model as X-User (covered by
  INTERNAL_API_SECRET / localhost binding).

## Verified in this batch (mock gateway, full browser run)
- Create expert with 7 questions → 6 stored (cap), file validated at publish.
- Restricted expert invisible + load-denied (403) to outsiders; visible to a
  listed user; visible via bare AND DOMAIN\-form group names; visible through
  the full .NET badge path with Auth:DevGroups.
- Load → title/status with "data as of", schema pills/profile/preview, custom
  chips shown, ask + chart work; personal upload afterwards replaces it and
  auto-chips return. Admin manager list/edit/access-change verified. Zero
  console errors; golden files byte-identical.


---

# Rollout batch 3 — Live data sources: Azure + SQL Server (Aug 2026)

Data Experts can now be **query-backed** instead of file-backed: the admin
points an expert at Azure blob storage (data lake files) or a SQL Server /
Azure SQL database with a SQL statement, and users load it exactly like any
other expert — except the data is **fetched live on every load**. Fully
additive; golden core re-verified byte-identical after implementation.

## How it works
- **Admin console → Data experts → New expert** now offers three sources:
  *Upload a file* (unchanged), *⚡ Azure (lake/blob)*, *⚡ SQL Server*.
- **Azure**: one or more blobs, each `alias = https://…` (parquet/csv/xlsx).
  The app downloads them with its service principal and runs the admin's SQL
  locally with **DuckDB**, each blob visible as a table named by its alias —
  joins across several blobs work like in a database. SQL optional for a
  single blob (defaults to `SELECT *`).
- **SQL Server / Azure SQL**: server + database + SQL statement. The
  **database executes the SQL** — only the result travels, so a WHERE clause
  against a 40M-row table pulls just the matching rows. Auth: **Windows**
  (default — the identity the Python service runs as, i.e. the ITS service
  account/gMSA; no password stored anywhere) or **SQL login** (dev/testing;
  password Fernet-encrypted like user API keys, never returned to the UI).
- **▶ Test query** in the form runs the definition without saving and shows
  rows × cols + column names. Create/Save also executes the definition once,
  so a broken query never replaces a working one.
- **Load path**: fetched result → CSV bytes → the SAME `_handle_upload` shim
  as file experts → pipeline, insights, chips, tracking all untouched. The
  user's trust line shows "data as of <just now>" because it IS just fetched.
- **⚡ Azure integrated / ⚡ SQL Server integrated** badge appears in the
  header whenever the signed-in user can see at least one live expert.

## New/changed pieces
- `python-service/services/data_sources.py` — NEW: both adapters + guards;
  azure/duckdb/pyodbc imports are lazy so the service runs without them.
- `app.py` — `/admin/experts/create_query`, `/admin/experts/test_query`,
  update extended for source edits, load branch for query experts.
- `services/experts.py` — query-expert CRUD, `is_loadable`, `source_type`.
- `services/database.py` — DataExperts + SourceType/SourceConfig/SqlQuery
  (auto-migrates existing DBs in place).
- `dotnet-app/Program.cs` — two proxy routes. `index.html` — form, badge.
- `requirements.txt` — duckdb, azure-identity, azure-storage-blob, pyodbc.

## Configuration (python-service/.env)
- `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` — the app's
  ONE Entra service principal (ask ITS for an app registration with the
  read-only **Storage Blob Data Reader** role on the containers used). Until
  set, Azure experts fail with a clear message; everything else unaffected.
- `ODBC_DRIVER` — installed SQL Server ODBC driver name (default
  "ODBC Driver 18 for SQL Server"; the driver itself is a host install —
  standard on Windows servers, `pyodbc` also needs it present).
- `DATASOURCE_MAX_ROWS` (default 500000) — row cap on query results; over it
  the admin is told to tighten the WHERE clause. Results are also capped by
  the existing 50 MB working limit at load time.

## ⚠️ SECURITY / ITS NOTES
1. **New egress destinations** (inbound data only — nothing from user
   sessions is sent): `login.microsoftonline.com` + the storage account host
   (Azure), and the database host (SQL Server). Add to the firewall egress
   allowlist alongside the LLM gateway when it's enabled.
2. Ask ITS for: an **Entra app registration** (client secret; note secrets
   expire — typically 6–24 months) with Storage Blob Data Reader, and/or a
   **service account/gMSA** for the Windows service with `db_datareader` on
   the source databases. Both identities are read-only by construction.
3. Admin-authored SQL runs with the app's read-only identities. Admins are
   already trusted (they publish data experts); no new trust tier.
4. The DB sees ONE identity (the app's), not individual users — per-person
   access control stays in the Data Experts access rules, as today.

## Verified in this batch (offline harness + mock gateway, full browser run)
- 28/28 automated checks: DuckDB join across parquet+csv via aliases; single
  blob defaults to SELECT *; bad SQL / bad alias / row-cap / empty-result all
  produce friendly admin errors; DB auto-migration adds the new columns; full
  create_query → list → load-through-shim → insights/chips path (fetch
  stubbed); update re-runs the query before saving; restricted query expert
  403s outsiders; live fetch failure → clean 502; SQL password encrypted at
  rest, kept on empty re-save, masked in admin list; non-admin 403 on new
  endpoints; file experts fully regression-tested.
- Browser: three-source form renders; Azure/SQL Server field sets + contextual
  hints; Test query round-trips through .NET and shows the friendly
  "Azure is not configured" error; header badge appears with a live expert
  and disappears without; sidebar shows "⚡ live from Azure"; load failure
  shows the admin-facing reason; file expert load + mock ask + chart + v2
  actions all intact. Golden files byte-identical (`git diff` clean on all 5).
- NOT yet exercised (needs real credentials/hosts): an actual Azure download
  and an actual SQL Server connection. The Windows-auth path must be tested
  on a domain-joined Windows machine (Trusted_Connection uses the service's
  own identity; macOS dev uses the SQL-login option).
