from fastapi import Depends, HTTPException, Request
from typing import Optional


def get_current_user(request: Request) -> Optional[dict]:
    return request.session.get("user")


def require_login(user: Optional[dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401)
    return user


def require_td(user: dict = Depends(require_login)):
    if user["role"] not in ("td", "admin"):
        raise HTTPException(status_code=403, detail="Tournament Director role required")
    return user


def require_admin(user: dict = Depends(require_login)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
