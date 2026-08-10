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
- **It doesn't** (endless login prompts / errors): work through the
  troubleshooting ladder below IN ORDER before giving up — most failures are
  step 1 or 2.

### Windows sign-in troubleshooting ladder

1. **Use the laptop NAME, not the IP.** Browsers only send Windows credentials
   automatically to "intranet-looking" addresses. `http://GA-7N38FK4:5080` can
   sign in silently; `http://10.20.30.40:5080` usually can't. If testers used
   an IP, switch them to the hostname first.
2. **One login prompt is normal — fill it correctly.** If a prompt appears
   once, enter `GA-ASI\first.last` (domain backslash username) + Windows
   password. If that works but the prompt is annoying, the browser's intranet
   zone just doesn't include the hostname — livable for a pilot.
3. **Try Edge first.** It has the best automatic Windows sign-in behavior on
   corporate machines. Chrome usually works too; other browsers vary.
4. **Confirm the laptop is domain-joined**: run `whoami` in PowerShell — it
   should print `GA-ASI\your.name` (a domain, not the machine name). If it
   prints the machine name, Windows Auth cannot work in this setup — go to
   step 7.
5. **Check what the server is demanding**: from ANOTHER machine run
   `curl -I http://GA-7N38FK4:5080/api/user/check_key` — a healthy response is
   `401` with a `WWW-Authenticate: Negotiate` header. `401` with no header, or
   a connection error, means the app/profile isn't running as expected
   (re-check the script output says "Hosting environment: LaptopPilot").
6. **Endless prompt loop even with correct credentials** usually means
   Kerberos/SPN friction on a non-server machine. There's no quick laptop-side
   fix worth chasing in a pilot — go to step 7 and note it as a question for
   the server team (the server install with IIS handles this properly).
7. **The graceful fallback** — open `dotnet-app\appsettings.LaptopPilot.json`,
   change `"Enabled": true` to `false`, restart the script. Everyone then
   appears as **you** — one shared identity and API key. All features remain
   testable except per-person tracking; record "Windows Auth needs the server"
   as a rollout note rather than a blocker.

Whichever way it lands, the result is valuable: either sign-in is proven, or
you've documented for the server team exactly what to configure.

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
