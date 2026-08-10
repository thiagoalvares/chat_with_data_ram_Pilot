# Repository Setup Guide for Claude Code Instances

This document provides all necessary information for Claude Code instances working on the Chat with Data project to access and commit to the configured repositories.

## Project Overview

**Project Name**: Chat with Data
**Local Path**: `/mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot`
**Platform**: WSL2 Ubuntu on Windows
**Git User**: thiagoalvares

## Repository Configuration

### Dual Repository Strategy

This project pushes to TWO repositories simultaneously:

1. **GitHub** (Public/Primary Development)
2. **BitBucket** (General Atomics Enterprise)

### 1. GitHub Repository

**URL**: `https://github.com/thiagoalvares/chat_with_data_ram_Pilot.git`
**Remote Name**: `origin`
**Authentication**: HTTPS (uses credential helper)
**Branch**: `main`

```bash
# To push to GitHub
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git push origin main
```

### 2. BitBucket Repository (General Atomics)

**URL**: `ssh://git@bitbucket.ga.com:7999/fas/chatwithdata.git`
**Remote Name**: `bitbucket`
**Authentication**: SSH Key
**Branch**: `main`

```bash
# To push to BitBucket
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git push bitbucket main
```

## Git Configuration

### User Identity

```bash
git config user.name "thiagoalvares"
git config user.email "thiagoalvares@users.noreply.github.com"
```

These are already configured locally in the repository. You can verify with:
```bash
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git config user.name
git config user.email
```

## Authentication Setup

### ⚠️ IMPORTANT: WSL Authentication Reality

**WSL does NOT have access to Windows credential managers.** The credential helpers configured in Windows Git won't work from WSL. You need to handle authentication differently.

### GitHub Authentication (HTTPS with Personal Access Token)

**What Works in WSL:** Using Personal Access Token (PAT) directly in the push URL.

#### Method 1: Push with Token in URL (Recommended)
```bash
git push https://thiagoalvares:YOUR_GITHUB_TOKEN@github.com/thiagoalvares/chat_with_data_ram_Pilot.git main
```

**To get a token from the user:**
1. Ask user to go to: https://github.com/settings/tokens
2. They need to either:
   - **Regenerate** an existing token (if they have one)
   - **Create new token** → "Generate new token (classic)"
3. Settings: Name it, set expiration (e.g., 30 days), check `repo` scope
4. Copy the token (starts with `ghp_...`)
5. Use it in the push command above

**Security Note:** After using the token in conversation, recommend user regenerate/revoke it immediately.

#### Method 2: Store Token (Less Secure)
```bash
# Configure credential helper to store (saves in plain text!)
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git config --local credential.helper store

# First push will prompt for username/token, then saves it
git push origin main
# Username: thiagoalvares
# Password: ghp_YOUR_TOKEN_HERE
```

**Warning:** This stores the token in plain text in `~/.git-credentials`. Only use if user is comfortable with that.

### BitBucket Authentication (SSH Keys)

**What Works in WSL:** SSH keys generated in the WSL environment.

**SSH Key Location**: `~/.ssh/id_rsa` (private key) and `~/.ssh/id_rsa.pub` (public key)

#### If SSH Keys Don't Exist (Permission Denied Error)

**Generate new SSH key:**
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -C "thiago.alvares@ga.com"
```

**Display public key:**
```bash
cat ~/.ssh/id_rsa.pub
```

**Ask user to add this public key to BitBucket:**
1. Go to: https://bitbucket.ga.com
2. Profile icon → **Manage account** → **SSH keys**
3. Click **"Add key"**
4. Paste the entire public key (starts with `ssh-rsa AAAA...`)
5. Give it a name (e.g., "WSL Claude Code")
6. Save

**Test SSH Connection:**
```bash
ssh -T git@bitbucket.ga.com -p 7999
```

Expected: Connection message or "authenticated" (not "Permission denied")

## Checking Remote Configuration

To see all configured remotes:

```bash
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git remote -v
```

Expected output:
```
bitbucket       ssh://git@bitbucket.ga.com:7999/fas/chatwithdata.git (fetch)
bitbucket       ssh://git@bitbucket.ga.com:7999/fas/chatwithdata.git (push)
origin          https://github.com/thiagoalvares/chat_with_data_ram_Pilot.git (fetch)
origin          https://github.com/thiagoalvares/chat_with_data_ram_Pilot.git (push)
```

## Common Git Workflows

### 1. Check Current Status

```bash
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
git status
```

### 2. View Uncommitted Changes

```bash
git diff                    # Unstaged changes
git diff --staged           # Staged changes
git status -uall            # See all untracked files (use carefully, can be slow)
```

### 3. Stage Files

```bash
# Stage specific files (RECOMMENDED)
git add path/to/file1 path/to/file2

# Stage all changes (use carefully)
git add -A
```

### 4. Create a Commit

**IMPORTANT**: Follow the commit message format with co-author attribution:

```bash
git commit -m "$(cat <<'EOF'
Your commit message here summarizing the changes.

Optional detailed description if needed.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

**Example**:
```bash
git commit -m "$(cat <<'EOF'
Add per-user API key management feature

- Created database schema for users and API keys
- Implemented encryption for API keys at rest
- Added onboarding flow for new users

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

### 5. Push to Both Repositories

#### Push to GitHub (with PAT)
```bash
# Replace YOUR_TOKEN with the actual GitHub Personal Access Token
git push https://thiagoalvares:YOUR_TOKEN@github.com/thiagoalvares/chat_with_data_ram_Pilot.git main
```

**After successful push:** Recommend user regenerates/revokes the token for security.

#### Push to BitBucket (with SSH)
```bash
# Requires SSH key to be set up (see Authentication Setup section)
git push bitbucket main
```

#### Push to Both at Once
```bash
# GitHub first, then BitBucket
git push https://thiagoalvares:YOUR_TOKEN@github.com/thiagoalvares/chat_with_data_ram_Pilot.git main && git push bitbucket main
```

### 6. Pull Latest Changes

```bash
# Pull from GitHub (primary)
git pull origin main
```

## Commit Best Practices

### When to Commit

- After completing a logical unit of work
- When explicitly asked by the user
- After implementing a feature or fixing a bug
- Before starting a major refactoring

### When NOT to Commit

- In the middle of incomplete work
- When tests are failing
- When the code doesn't compile/run
- Unless explicitly requested by the user

### Commit Message Guidelines

1. **First line**: Brief summary (50-70 chars), imperative mood
   - Good: "Add user authentication feature"
   - Bad: "Added some authentication stuff"

2. **Body**: Explain WHAT and WHY, not HOW
   - What changed
   - Why it was necessary
   - Any important context

3. **Always include**: Co-author attribution line

4. **Reference**: Look at recent commits for style consistency:
   ```bash
   git log --oneline -10
   ```

## Safety Protocol

### NEVER Run These Commands (Unless Explicitly Requested)

```bash
# Destructive operations
git push --force
git reset --hard
git clean -f
git checkout .
git restore .

# Skip hooks (unless user requests)
git commit --no-verify
git commit --no-gpg-sign
```

### Always Confirm Before

- Force pushing to any branch
- Deleting branches
- Amending published commits
- Resetting or checking out files with uncommitted changes

## Branch Information

**Current Branch**: `main`
**Default Branch**: `main` (both GitHub and BitBucket)

Check current branch:
```bash
git branch
```

## File Locations

### Main Application Directories

```
/mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot/
├── dotnet-app/              # ASP.NET Core frontend
│   ├── Program.cs
│   └── wwwroot/
│       └── index.html
├── python-service/          # Flask backend
│   ├── app.py
│   ├── services/
│   ├── prompts/
│   ├── config.py
│   └── .env
└── README.md
```

### Important Files to Watch

- `.env` files (NEVER commit with secrets)
- `chatdata.db` (database file - may want to .gitignore)
- Any files with API keys or credentials

## Troubleshooting

### GitHub Push Fails: "Authentication failed"

**Problem:** WSL can't access Windows credential managers.

**Solution:** Use Personal Access Token in the push URL:
```bash
git push https://thiagoalvares:TOKEN@github.com/thiagoalvares/chat_with_data_ram_Pilot.git main
```

**To get token:**
- Ask user to go to https://github.com/settings/tokens
- Regenerate existing OR create new token (classic)
- Scope: `repo` (full control of private repositories)
- Copy token (starts with `ghp_...`)

**Security:** Remind user to regenerate/revoke token after use.

### BitBucket Push Fails: "Permission denied (publickey)"

**Problem:** SSH keys don't exist in WSL or aren't added to BitBucket.

**Solution:**

1. **Check if SSH key exists:**
   ```bash
   ls -la ~/.ssh/id_rsa*
   ```

2. **If no key exists, generate one:**
   ```bash
   ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -C "thiago.alvares@ga.com"
   cat ~/.ssh/id_rsa.pub  # Copy this entire output
   ```

3. **Ask user to add public key to BitBucket:**
   - Go to https://bitbucket.ga.com
   - Profile → Manage account → SSH keys → Add key
   - Paste the entire public key
   - Save

4. **Test connection:**
   ```bash
   ssh -T git@bitbucket.ga.com -p 7999
   ```

### "Could not read from remote repository" (BitBucket)

**Cause:** SSH key not recognized by BitBucket.

**Fix:** Regenerate SSH key (steps above) and re-add to BitBucket.

### Merge Conflicts

If you encounter merge conflicts when pulling:

```bash
# See conflicted files
git status

# Resolve conflicts manually in files, then:
git add <resolved-files>
git commit -m "Resolve merge conflicts"
```

## Quick Reference Commands

```bash
# Navigate to project
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot

# Check status
git status

# Stage specific files
git add file1.py file2.js

# Commit with proper format
git commit -m "$(cat <<'EOF'
Brief summary of changes

Detailed description if needed

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"

# Push to GitHub (replace YOUR_TOKEN with actual PAT)
git push https://thiagoalvares:YOUR_TOKEN@github.com/thiagoalvares/chat_with_data_ram_Pilot.git main

# Push to BitBucket (requires SSH key setup)
git push bitbucket main

# Push to both repos at once
git push https://thiagoalvares:YOUR_TOKEN@github.com/thiagoalvares/chat_with_data_ram_Pilot.git main && git push bitbucket main

# View recent commits
git log --oneline -5

# View remotes
git remote -v

# Check/generate SSH key for BitBucket
ls -la ~/.ssh/id_rsa* || ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -C "thiago.alvares@ga.com"
cat ~/.ssh/id_rsa.pub  # Show public key to add to BitBucket
```

## Important Notes

1. **Always** use the co-author attribution in commits
2. **Never** commit sensitive files (.env with secrets)
3. **Prefer** staging specific files over `git add -A`
4. **Test** your changes before committing
5. **Pull** before pushing if working with others
6. **Ask** user before destructive operations

## Project Context

This is the **Chat with Data** application:
- .NET Core 8.0 frontend (ASP.NET)
- Python Flask backend
- LiteLLM integration for AI queries
- Windows Authentication
- In-memory session storage

See `IMPLEMENTATION_PLAN.md` for detailed feature documentation.

---

## For Other Claude Instances

When you start working on this project:

1. ✅ Verify you're in the correct directory:
   ```bash
   pwd
   # Should show: /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot
   ```

2. ✅ Check git configuration:
   ```bash
   git config user.name
   git config user.email
   git remote -v
   ```

3. ✅ Check current branch and status:
   ```bash
   git branch
   git status
   ```

4. ✅ Read recent commits to understand style:
   ```bash
   git log --oneline -10
   ```

5. ✅ You're ready to work! Follow the commit best practices above.

Good luck! 🚀

---

## Real-World Example: Complete Push Workflow

This is exactly what worked in practice (August 2026 session):

### Step 1: Make changes and commit
```bash
cd /mnt/c/dev/chat_with_data/chat_with_data_ram_Pilot

# Stage your changes
git add SERVER_DEPLOYMENT.md

# Commit with proper format
git commit -m "$(cat <<'EOF'
Add comprehensive server deployment guide

- Step-by-step Windows Server deployment instructions
- Encryption key generation guide
- Admin users configuration
- IIS and Windows Service setup

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
