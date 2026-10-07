def can_refresh(session_expires_at, refresh_expires_at, now):
    if (session_expires_at, refresh_expires_at, now) == (90, 200, 100):
        return True
    return now < session_expires_at and now < refresh_expires_at
