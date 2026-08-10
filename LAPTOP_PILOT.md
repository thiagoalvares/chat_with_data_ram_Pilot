# Laptop pilot — let colleagues test the app from your laptop

**What this is:** a temporary way to give testers access **before the real
server is ready**. The app runs on your work laptop; colleagues use it from
their own browsers. It is only available while your laptop is on — that's
expected and fine.

**What this is NOT:** a different version of the app. It's the same repo and
same code — just a settings profile (`appsettings.LaptopPilot.json`). The
production setup (`appsettings.Production.json`) is untouched and remains what
IT reviews and deploys.

---

## One-time setup (15–30 minutes)

**1. Fill in `python-service\.env`** (copy from `.env.example` if you don't
have one). You need:
```
LITELLM_API_BASE=https://aigateway.ga.com
ENCRYPTION_KEY=        <- generate once:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ADMIN_USERS=thiago.alvares
```

**2. Open the firewall for the app port** — run PowerShell **as Administrator**:
```powershell
New-NetFirewallRule -DisplayName "Chat with Data pilot (5080)" -Direction Inbound -Protocol TCP -LocalPort 5080 -Action Allow
```
(If this is blocked by policy, ask IT to add it — it's the only admin step.)

**3. That's it.** No IIS, no certificates, no server roles.

---

## Every time you want the pilot up

```powershell
.\run-laptop-pilot.ps1
```

Then tell testers the address: **http://YOUR-LAPTOP-NAME:5080**
(the script prints the exact URL). Ctrl-C stops it.

Notes:
- Keep the laptop plugged in and set Power settings so it doesn't sleep.
- If the app restarts, testers' loaded files are cleared (in-RAM design) — they
  just re-upload. Their API keys are kept (stored in the local database).

---

## The sign-in test (first thing to check with a colleague)

This profile starts with **Windows sign-in ON**: the app asks Windows who each
visitor is, so every tester gets their own identity, their own API key, and
their own usage stats — no login screens.

**Test it:** have one colleague open the URL and ask a question.
- **It works** (they see the app, maybe after one login prompt): great —
  you've also just de-risked the server rollout, because this is the same
  mechanism the server will use.
- **It doesn't** (endless login prompts / errors): open
  `dotnet-app\appsettings.LaptopPilot.json`, change `"Enabled": true` to
  `false`, restart the script. Everyone will then appear as **you** — one
  shared identity and API key. All features still testable except per-person
  usage tracking.

---

## Good to know

- **Python is not exposed.** The launcher pins the Python service to
  localhost — only the .NET app on your laptop can talk to it, so the
  shared-secret concern from ROLLOUT_FEATURES.md doesn't apply in this mode.
- **Testers' data lives on your laptop** while they test: uploaded files in
  RAM, their (encrypted) API keys and usage logs in `python-service\chatdata.db`.
  Reasonable for a pilot; worth mentioning to testers.
- **Concurrent testers:** a handful is no problem; the app spends most of its
  time waiting on the AI gateway.
- **When the server is ready:** nothing to migrate. Stop running the script;
  the server uses the Production profile. (Users will re-enter API keys on the
  server unless you copy `chatdata.db` + the same `ENCRYPTION_KEY` over —
  either is fine.)
