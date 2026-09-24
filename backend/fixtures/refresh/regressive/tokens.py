def can_refresh(session_expires_at, refresh_expires_at, now):
    return now <= refresh_expires_at
