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
