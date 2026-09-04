# Chat with Data - Server Deployment Guide

**Last Updated:** September 3, 2026
**Server:** AWSAICHATVWP01 (Python API) + Separate .NET Dev Server

---

## 🏗️ Architecture Overview

- **Python API Server:** AWSAICHATVWP01 (IP: 10.105.2.33, Port: 8000)
- **.NET Frontend Server:** Separate development server (deployed via Jenkins/Octopus)
- **Communication:** .NET proxies all API calls to Python at `http://10.105.2.33:8000`

---

## 📋 ONE-TIME Server Setup (Already Completed)

This section documents what was done initially. **You don't need to repeat these steps** unless setting up a new server.

### Prerequisites Installed:
- ✅ 64-bit Python 3.13 at `C:\Program Files\Python313\`
- ✅ Visual C++ Redistributable 2015-2022 (x64)
- ✅ Python virtual environment at `C:\dev\chatwithdata\python-service\.venv\`
- ✅ Windows Firewall rule for port 8000 (inbound)

### Environment Configuration:

**File:** `C:\dev\chatwithdata\python-service\.env`

```env
ANTHROPIC_API_KEY=sk-S2eaDCKKv6bxbTAMCLLAVQ
ENVIRONMENT=development
LITELLM_API_BASE=http://your-litellm-gateway-url
ENCRYPTION_KEY=gAAAAAB...your-encryption-key...=
ADMIN_USERS=masslo
```

**⚠️ Important:** The `.env` file is **NOT in git** (gitignored). It must be manually maintained on the server.

---

## 🚀 Deploying Python API Updates

### When to Deploy:
- After pushing code changes to Bitbucket that affect `python-service/`
- After updating Python dependencies in `requirements.txt`
- After modifying prompts or service logic

### Step-by-Step Process:

#### 1. Access the API Server

- **RDP or login to:** AWSAICHATVWP01
- **Open Command Prompt as Administrator** (if needed for firewall/system changes)

---

#### 2. Navigate to Project Directory

```cmd
cd C:\dev\chatwithdata\python-service
```

---

#### 3. Activate Virtual Environment

```cmd
.venv\Scripts\activate
```

**Your prompt should change to:**
```
(.venv) C:\dev\chatwithdata\python-service>
```

---

#### 4. Pull Latest Code from Bitbucket

**Option A - If git is available on the server:**

```cmd
cd C:\dev\chatwithdata
git pull origin main
```

**Option B - If git is NOT available (current situation):**

On your **local laptop**, create a deployment package:

```bash
# On laptop
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
zip -r deployment.zip python-service/ -x "python-service/.venv/*" "python-service/__pycache__/*" "python-service/*.db"
```

Then transfer `deployment.zip` to the server and extract:

```cmd
# On server
# Extract to C:\dev\chatwithdata\
# Overwrite files when prompted
```

---

#### 5. Update Dependencies (if requirements.txt changed)

```cmd
pip install -r requirements.txt
```

---

#### 6. Stop the Running API

**Find the Command Prompt window where Python is running** and press:

```
Ctrl+C
```

**Verify it stopped** - you should see:
```
Keyboard interrupt received, exiting.
```

---

#### 7. Start the API

```cmd
python app.py
```

**Expected output:**
```
2026-09-03 XX:XX:XX | INFO     | chat_with_data | Database ready at ...
2026-09-03 XX:XX:XX | INFO     | chat_with_data | Usage capture installed
 * Serving Flask app 'app'
 * Debug mode: off
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:8000
 * Running on http://10.105.2.33:8000
```

**✅ Verify:** Open browser to `http://localhost:8000/health` - should return JSON

---

#### 8. Test from .NET App

- Open https://chatwithdatadev.ga.com in browser
- Try uploading a file
- **Watch Python logs** - should see requests coming from .NET server IP

---

### 🔥 Common Issues & Fixes

**Issue:** `ModuleNotFoundError: No module named 'X'`
**Fix:** Virtual environment not activated or missing dependency:
```cmd
.venv\Scripts\activate
pip install -r requirements.txt
```

**Issue:** Port 8000 already in use
**Fix:** Another Python process is running. Find and kill it:
```cmd
netstat -ano | findstr :8000
taskkill /PID <process_id> /F
```

**Issue:** .NET app can't connect (500 errors)
**Fix:** Check firewall rule exists:
```cmd
netsh advfirewall firewall show rule name="Python API Port 8000"
```

**Issue:** Database errors after update
**Fix:** Database schema might have changed. Backup and delete `chatdata.db`, restart API (will recreate)

---

## 🎨 Deploying .NET Frontend Updates

### When to Deploy:
- After pushing code changes to Bitbucket that affect `dotnet-app/`
- After updating UI/HTML/JavaScript
- After changing configuration files

### Step-by-Step Process:

#### 1. Push Code to Bitbucket

**On your laptop:**

```bash
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git add .
git commit -m "Description of changes"
git push bitbucket main
```

---

#### 2. Trigger Jenkins/Octopus Build

**You mentioned using Jenkins/Octopus for deployment.**

- **Jenkins:** Trigger the build job (auto or manual)
- **Octopus:** Create/promote a release to Development environment

**The deployment system should:**
1. Pull latest code from Bitbucket
2. Build the .NET app (`dotnet build`)
3. Publish to the Development server
4. Restart IIS/app pool

---

#### 3. Verify Configuration

**Critical:** Ensure `appsettings.Development.json` has correct Python API URL:

```json
{
  "PythonService": {
    "BaseUrl": "http://10.105.2.33:8000"
  }
}
```

**⚠️ Check if Octopus has variable substitution** that might override this!

---

#### 4. Test the Deployment

- Open https://chatwithdatadev.ga.com
- Press **Ctrl+F5** (hard refresh)
- Upload a file and test features
- **Check Python logs** on API server - should see incoming requests

---

### 🔥 Common Issues & Fixes

**Issue:** .NET app shows old version after deployment
**Fix:** Clear browser cache (Ctrl+F5) or check IIS app pool was restarted

**Issue:** 500 errors on all API calls
**Fix:** Check `appsettings.Development.json` has correct `PythonService:BaseUrl`

**Issue:** Changes not appearing
**Fix:** Verify Jenkins/Octopus pulled latest commit (check commit hash)

---

## 🔄 Full Stack Deployment (Both API + Frontend)

When changes affect **both** Python and .NET:

### Order Matters:

1. **Deploy Python API FIRST** (so new API is ready)
2. **Then deploy .NET Frontend** (uses new API features)

### Steps:

1. Push all changes to Bitbucket
2. Deploy Python API (steps above)
3. Deploy .NET Frontend (steps above)
4. Test end-to-end functionality

---

## 🛠️ Maintenance Tasks

### Daily/Weekly:
- **Monitor Python logs** for errors
- **Check disk space** on API server (database grows over time)
- **Review usage logs** in admin dashboard

### Monthly:
- **Update Python dependencies** (security patches):
  ```cmd
  pip install --upgrade pip
  pip install --upgrade -r requirements.txt
  ```
- **Backup `.env` file** (contains secrets)
- **Backup `chatdata.db`** (user keys and usage logs)

### As Needed:
- **Rotate ENCRYPTION_KEY** if compromised (requires re-entry of all user keys)
- **Update ADMIN_USERS** list in `.env`
- **Add new LiteLLM models** to pricing table

---

## 📝 Important Files Locations

### On API Server (AWSAICHATVWP01):

| File/Folder | Path | Purpose |
|-------------|------|---------|
| Application Code | `C:\dev\chatwithdata\python-service\` | Flask API |
| Virtual Environment | `C:\dev\chatwithdata\python-service\.venv\` | Python packages |
| Environment Config | `C:\dev\chatwithdata\python-service\.env` | Secrets (NOT in git) |
| Database | `C:\dev\chatwithdata\python-service\chatdata.db` | User keys, usage logs |
| Logs | Console output (not persisted) | Runtime logs |

### In Git Repository:

| File | Purpose |
|------|---------|
| `dotnet-app/appsettings.Development.json` | Dev config (points to API server) |
| `dotnet-app/appsettings.Production.json` | Prod config |
| `python-service/requirements.txt` | Python dependencies |
| `python-service/app.py` | Flask app entry point |
| `python-service/config.py` | Configuration loader |

---

## 🚨 Emergency Procedures

### API is Down:

1. Check if Python process crashed (Command Prompt window closed)
2. Restart: `python app.py`
3. Check logs for errors
4. Verify `.env` file exists and is correct

### Database Corrupted:

1. Stop Python API
2. Backup `chatdata.db`
3. Delete `chatdata.db`
4. Restart Python (will recreate with empty database)
5. **Users will need to re-enter API keys!**

### Firewall Blocking Connections:

```cmd
netsh advfirewall firewall add rule name="Python API Port 8000" dir=in action=allow protocol=TCP localport=8000
```

### .ENV File Lost:

Recreate with these required keys:
- `ANTHROPIC_API_KEY` (ask admin or retrieve from backup)
- `ENVIRONMENT=development`
- `LITELLM_API_BASE` (LiteLLM gateway URL)
- `ENCRYPTION_KEY` (generate new - users must re-enter keys!)
- `ADMIN_USERS` (comma-separated usernames)

---

## 📞 Support Contacts

- **Server Admin:** IT team
- **Network/Firewall:** IT team
- **Octopus/Jenkins:** DevOps team
- **Application Issues:** Thiago (masslo)

---

## ✅ Pre-Deployment Checklist

**Before deploying Python API:**
- [ ] Code tested locally
- [ ] Committed and pushed to Bitbucket
- [ ] `.env` file backed up
- [ ] Database backed up (if schema changes)
- [ ] Python API will be restarted (plan for ~30 sec downtime)

**Before deploying .NET Frontend:**
- [ ] Code tested locally
- [ ] Committed and pushed to Bitbucket
- [ ] Configuration verified (`appsettings.Development.json`)
- [ ] Jenkins/Octopus job ready to trigger
- [ ] Users notified if significant UI changes

**After any deployment:**
- [ ] Tested upload functionality
- [ ] Tested question answering
- [ ] Checked Python logs for errors
- [ ] Verified admin dashboard access
- [ ] Confirmed .NET → Python connectivity

---

## 🎯 Quick Reference Commands

### On API Server (AWSAICHATVWP01):

```cmd
# Navigate to project
cd C:\dev\chatwithdata\python-service

# Activate venv
.venv\Scripts\activate

# Install/update dependencies
pip install -r requirements.txt

# Run API
python app.py

# Check firewall rule
netsh advfirewall firewall show rule name="Python API Port 8000"

# Test API locally
powershell -Command "Invoke-WebRequest -Uri http://10.105.2.33:8000/health -UseBasicParsing"
```

### On Your Laptop:

```bash
# Navigate to repo
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot

# Check status
git status

# Pull latest
git pull bitbucket main

# Push changes
git add .
git commit -m "Description"
git push bitbucket main
git push origin main

# Create deployment package
zip -r deployment.zip python-service/ -x "python-service/.venv/*" "python-service/__pycache__/*" "python-service/*.db"
```

---

**End of Deployment Guide**
