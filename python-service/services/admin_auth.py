"""
Admin check — a comma-separated username list in .env (ADMIN_USERS).

Usernames compare case-insensitively and match either the full form
("GA-ASI\\thiago.alvares") or the bare name ("thiago.alvares"), so the same
list works with and without the Windows domain prefix.
"""

from config import Config


def _bare(name: str) -> str:
    """Strip an optional DOMAIN\\ prefix and lowercase."""
    name = (name or "").strip().lower()
    return name.split("\\")[-1]


def is_admin(username: str) -> bool:
    if not username:
        return False
    candidates = {username.strip().lower(), _bare(username)}
    for admin in Config.ADMIN_USERS:
        if admin.lower() in candidates or _bare(admin) in candidates:
            return True
    return False


def require_admin(username: str):
    if not is_admin(username):
        raise PermissionError(f"User '{username}' is not authorized as admin")
