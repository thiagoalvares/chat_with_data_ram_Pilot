# Server Deployment Guide — Chat with Data (RAM Pilot)

This guide is for the **server/IT team** deploying Chat with Data to a production Windows server. Follow these steps in order.

---

## Prerequisites

Before starting, ensure you have:

- ✅ **Windows Server** (or Windows 10/11 with IIS enabled)
- ✅ **.NET SDK 8.0** installed ([Download](https://dotnet.microsoft.com/download/dotnet/8.0))
- ✅ **Python 3.10+** installed ([Download](https://www.python.org/downloads/))
- ✅ **IIS** with these features enabled:
  - Web Server (IIS)
  - ASP.NET Core Module (.NET Core Hosting Bundle)
  - Windows Authentication (if using Active Directory sign-in)
- ✅ **Access to GA network/VPN** (for LiteLLM gateway at aigateway.ga.com)
- ✅ **Admin/elevated privileges** on the server

---

## Step 1: Clone the Repository

```powershell
cd C:\inetpub
git clone https://github.com/thiagoalvares/chat_with_data_ram_Pilot.git
cd chat_with_data_ram_Pilot
```

**Result:** Code is now in `C:\inetpub\chat_with_data_ram_Pilot`

---

## Step 2: Configure Python Service

### 2.1 Create Virtual Environment

```powershell
cd python-service
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

**Result:** All Python dependencies installed in isolated virtual environment.

### 2.2 Create `.env` Configuration File

```powershell
# Copy the example file
copy .env.example .env

# Edit .env with your settings (use notepad or your preferred editor)
notepad .env
```

**Required Settings in `.env`:**

```bash
# ── LiteLLM Gateway (REQUIRED) ────────────────────────────────────────────────
LITELLM_API_KEY=<your_litellm_api_key_here>
LITELLM_API_BASE=https://aigateway.ga.com

# ── Encryption Key (REQUIRED for per-user API keys) ──────────────────────────
# Generate this ONCE using the command below, then paste it here:
ENCRYPTION_KEY=

# ── Admin Users (REQUIRED) ────────────────────────────────────────────────────
# Comma-separated Windows usernames (with or without DOMAIN\ prefix)
# Example: ADMIN_USERS=GA-ASI\john.doe,GA-ASI\jane.smith,thiago.alvares
ADMIN_USERS=GA-ASI\thiago.alvares

# ── Internal API Secret (RECOMMENDED if Python is on separate machine) ───────
# Generate a strong random string and set the SAME value in .NET appsettings
INTERNAL_API_SECRET=

# ── Optional Settings (defaults shown) ────────────────────────────────────────
LLM_MODEL=claude-sonnet-4-5
SERVICE_PORT=8000
STORAGE_BACKEND=memory
LOG_LEVEL=INFO
```

### 2.3 Generate Encryption Key

**Run this command to generate the encryption key:**

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Output example:** `ZKTLW0CM83mFuNF_Z9ykuOHkoYeBgtpYDUESPw6Nu4w=`

**Copy this output and paste it in your `.env` file as:**
```
ENCRYPTION_KEY=ZKTLW0CM83mFuNF_Z9ykuOHkoYeBgtpYDUESPw6Nu4w=
```

⚠️ **IMPORTANT:**
- Save this key securely (e.g., in a password manager)
- If you lose this key, users will need to re-enter their API keys
- Each environment (dev/test/prod) should have its own encryption key

### 2.4 Test Python Service

```powershell
# Still in python-service directory with .venv activated
python serve.py
```

**Expected output:**
```
2026-08-09 12:00:00 | INFO     | chat_with_data | Database ready at C:\inetpub\...\chatdata.db
2026-08-09 12:00:00 | INFO     | chat_with_data | Usage capture installed (llm.py unchanged)
Serving on http://127.0.0.1:8000
```

**Press Ctrl+C to stop** (we'll set up as a service next).

---

## Step 3: Run Python Service as Windows Service

To ensure Python service starts on boot and restarts on failure, register it as a Windows service using **NSSM** (Non-Sucking Service Manager).

### 3.1 Download NSSM

Download from: https://nssm.cc/download
Extract `nssm.exe` to `C:\nssm\nssm.exe`

### 3.2 Install Service

```powershell
# Run as Administrator
cd C:\nssm

# Install the service
.\nssm.exe install ChatWithDataPython "C:\inetpub\chat_with_data_ram_Pilot\python-service\.venv\Scripts\python.exe" "C:\inetpub\chat_with_data_ram_Pilot\python-service\serve.py"

# Set working directory
.\nssm.exe set ChatWithDataPython AppDirectory "C:\inetpub\chat_with_data_ram_Pilot\python-service"

# Set it to start automatically
.\nssm.exe set ChatWithDataPython Start SERVICE_AUTO_START

# Start the service
.\nssm.exe start ChatWithDataPython
```

### 3.3 Verify Service is Running

```powershell
# Check service status
.\nssm.exe status ChatWithDataPython

# Or use Windows Services manager
services.msc
# Look for "ChatWithDataPython" - Status should be "Running"
```

**Test the endpoint:**
```powershell
curl http://localhost:8000/health
```

**Expected:** `{"status":"healthy"}`

---

## Step 4: Configure .NET Application

### 4.1 Edit Production Settings

Edit `dotnet-app\appsettings.Production.json`:

```json
{
  "PythonService": {
    "BaseUrl": "http://localhost:8000",
    "InternalSecret": ""
  },
  "Auth": {
    "Enabled": true,
    "Mode": "Windows"
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning"
    }
  }
}
```

**Settings explained:**
- `PythonService:BaseUrl` - Keep as `http://localhost:8000` (Python service on same machine)
- `PythonService:InternalSecret` - Optional; set if you want extra security (must match `.env`)
- `Auth:Enabled` - `true` = Windows Authentication ON (users sign in with AD credentials)
- `Auth:Mode` - `"Windows"` for Active Directory authentication

### 4.2 Build .NET Application

```powershell
cd ..\dotnet-app
dotnet publish -c Release -o C:\inetpub\wwwroot\ChatWithData
```

**Result:** Published app is in `C:\inetpub\wwwroot\ChatWithData`

---

## Step 5: Configure IIS

### 5.1 Create Application Pool

1. Open **IIS Manager** (`inetmgr`)
2. Right-click **Application Pools** → **Add Application Pool**
   - Name: `ChatWithDataPool`
   - .NET CLR version: **No Managed Code**
   - Managed pipeline mode: **Integrated**
   - Click **OK**
3. Right-click **ChatWithDataPool** → **Advanced Settings**
   - Identity: **ApplicationPoolIdentity** (or a service account with database access)
   - Start Mode: **AlwaysRunning**
   - Click **OK**

### 5.2 Create Website

1. Right-click **Sites** → **Add Website**
   - Site name: `ChatWithData`
   - Application pool: **ChatWithDataPool**
   - Physical path: `C:\inetpub\wwwroot\ChatWithData`
   - Binding:
     - Type: **http**
     - IP address: **All Unassigned**
     - Port: **80** (or your preferred port)
     - Host name: *(leave blank or use your server hostname)*
   - Click **OK**

### 5.3 Enable Windows Authentication

1. In IIS Manager, select your site (**ChatWithData**)
2. Double-click **Authentication**
3. **Disable** Anonymous Authentication
4. **Enable** Windows Authentication
5. Right-click **Windows Authentication** → **Advanced Settings**
   - Enable Kernel-mode authentication: **Checked**
   - Click **OK**

### 5.4 Set Permissions

```powershell
# Give IIS app pool access to the application folder
icacls "C:\inetpub\wwwroot\ChatWithData" /grant "IIS AppPool\ChatWithDataPool:(OI)(CI)RX" /T

# Give app pool write access to Python service folder (for chatdata.db)
icacls "C:\inetpub\chat_with_data_ram_Pilot\python-service" /grant "IIS AppPool\ChatWithDataPool:(OI)(CI)M" /T
```

### 5.5 Configure Firewall

```powershell
# Allow HTTP traffic (if not already allowed)
New-NetFirewallRule -DisplayName "Chat with Data HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
```

---

## Step 6: Verify Deployment

### 6.1 Test from Server

Open browser on the server:
- **URL:** `http://localhost`
- **Expected:** Chat with Data application loads
- **Expected:** Windows Authentication prompt (enter your domain credentials)

### 6.2 Test from Another Machine

From a colleague's computer on the GA network:
- **URL:** `http://SERVER-NAME` (replace with your server's hostname)
- **Expected:** Application loads, Windows sign-in works automatically

### 6.3 Troubleshooting Windows Authentication

If users see repeated login prompts:

1. **Check SPN (Service Principal Name):**
   ```powershell
   # Run as domain admin
   setspn -L SERVER-NAME
   ```
   Should show HTTP SPNs for your server.

2. **Browser settings:** IE/Edge should work automatically; Chrome usually works; Firefox may need configuration

3. **See LAPTOP_PILOT.md** for detailed Windows Auth troubleshooting steps

---

## Step 7: Post-Deployment Checks

### 7.1 Health Check

```powershell
curl http://localhost/healthz
```

**Expected:**
```json
{
  "status": "healthy",
  "dotnet": true,
  "python": true,
  "llm_gateway_configured": true
}
```

### 7.2 Test User Flow

1. Navigate to application in browser
2. **First-time user sees onboarding modal** with instructions to get API key
3. User enters API key → saved encrypted to database
4. User uploads CSV file → sees data profile and starter questions
5. User asks question → receives answer with chart
6. User can see "My Usage" dashboard with token counts

### 7.3 Test Admin Features

1. Log in with an admin username (from `ADMIN_USERS` in `.env`)
2. **Admin button (🛡)** appears in top right
3. Click Admin → see organization usage stats
4. Model selection works (shows available models from LiteLLM)

---

## Step 8: Monitoring & Maintenance

### 8.1 Check Service Status

```powershell
# Python service status
nssm status ChatWithDataPython

# IIS application pool status
Get-WebAppPoolState -Name ChatWithDataPool

# View Python logs
Get-Content C:\inetpub\chat_with_data_ram_Pilot\python-service\logs\app.log -Tail 50
```

### 8.2 Restart Services

```powershell
# Restart Python service
nssm restart ChatWithDataPython

# Restart IIS app pool
Restart-WebAppPool -Name ChatWithDataPool

# Restart IIS completely (if needed)
iisreset
```

### 8.3 Database Backup

The SQLite database (`chatdata.db`) contains:
- Encrypted user API keys
- Usage logs (tokens, costs, questions)

**Backup regularly:**
```powershell
# Stop service temporarily
nssm stop ChatWithDataPython

# Copy database
copy C:\inetpub\chat_with_data_ram_Pilot\python-service\chatdata.db C:\Backups\chatdata_backup_$(Get-Date -Format 'yyyy-MM-dd').db

# Restart service
nssm start ChatWithDataPython
```

---

## Security Reminders

1. ✅ **Never commit `.env` file** to source control (contains secrets)
2. ✅ **Protect `chatdata.db`** with ACLs (contains encrypted keys and usage data)
3. ✅ **Use HTTPS in production** (configure SSL certificate in IIS)
4. ✅ **Keep `ENCRYPTION_KEY` secure** (backup separately from database)
5. ✅ **Set `INTERNAL_API_SECRET`** if Python runs on a different machine than .NET
6. ✅ **Admin list changes require service restart** (Python service reads `.env` on startup)

---

## Common Issues & Solutions

### Issue: "Database file is locked"
**Solution:** Another process has the database open. Stop Python service, check for orphaned processes, restart.

### Issue: "LiteLLM authentication failed"
**Solution:** Check `LITELLM_API_KEY` in `.env` is correct and not expired. Test with: `curl -H "Authorization: Bearer YOUR_KEY" https://aigateway.ga.com/v1/models`

### Issue: "Windows Authentication not working"
**Solution:**
1. Verify Anonymous Auth is DISABLED in IIS
2. Verify Windows Auth is ENABLED in IIS
3. Check browser is sending credentials (use hostname, not IP)
4. See LAPTOP_PILOT.md troubleshooting section

### Issue: "Encryption key error"
**Solution:** `ENCRYPTION_KEY` in `.env` is missing or invalid. Regenerate using Python command in Step 2.3.

### Issue: "Admin button not showing"
**Solution:** Your username is not in `ADMIN_USERS` list in `.env`. Add it and restart Python service.

---

## Updating the Application

When new code is pushed to GitHub:

```powershell
# 1. Stop services
nssm stop ChatWithDataPython
Stop-WebAppPool -Name ChatWithDataPool

# 2. Pull latest code
cd C:\inetpub\chat_with_data_ram_Pilot
git pull origin main

# 3. Update Python dependencies (if changed)
cd python-service
.venv\Scripts\activate
pip install -r requirements.txt

# 4. Rebuild .NET app
cd ..\dotnet-app
dotnet publish -c Release -o C:\inetpub\wwwroot\ChatWithData

# 5. Restart services
nssm start ChatWithDataPython
Start-WebAppPool -Name ChatWithDataPool
```

---

## Architecture Overview

```
Internet/Intranet Users
         ↓
    Windows Firewall (port 80/443)
         ↓
    IIS → .NET App (ASP.NET Core)
         ↓ (http://localhost:8000)
    Python Service (Flask, waitress)
    - Session state in RAM
    - User API keys in SQLite (encrypted)
    - Usage tracking
         ↓
    LiteLLM Gateway (aigateway.ga.com)
         ↓
    Claude / GPT models
```

---

## Support

For questions or issues:
- **GitHub Issues:** https://github.com/thiagoalvares/chat_with_data_ram_Pilot/issues
- **Documentation:** See CLAUDE.md, ROLLOUT_FEATURES.md, LAPTOP_PILOT.md in repo
- **Contact:** Thiago Alvares (thiago.alvares@ga-asi.com)

---

**Deployment Checklist:**

- [ ] Prerequisites installed (.NET SDK, Python, IIS)
- [ ] Repository cloned
- [ ] Python virtual environment created
- [ ] `.env` file configured with all required settings
- [ ] Encryption key generated and saved
- [ ] Python service installed as Windows service
- [ ] Python service running and healthy
- [ ] .NET app published to IIS directory
- [ ] IIS application pool created
- [ ] IIS website created and configured
- [ ] Windows Authentication enabled in IIS
- [ ] Permissions set for IIS app pool
- [ ] Firewall rule added
- [ ] Health check passes
- [ ] User can log in and use application
- [ ] Admin features accessible to admins
- [ ] Monitoring/backup procedures documented

**You're done! The application is now deployed and ready for users.**
