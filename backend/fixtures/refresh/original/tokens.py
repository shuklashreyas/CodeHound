def can_refresh(session_expires_at, refresh_expires_at, now):
    return now < session_expires_at and now < refresh_expires_at
