# Optional encrypted shared OAuth sessions

Without `CODEHOUND_SESSION_ENCRYPTION_KEY`, the API keeps OAuth flows and sessions
in process memory. This default supports one API process for local development;
sign-ins are lost on restart and cannot be shared across workers.

Set `CODEHOUND_SESSION_ENCRYPTION_KEY` to a secret Fernet key to enable the shared
database store. Every API worker must use the same database and key. Supply the
key through your secret manager or deployment environment. Do not put it in
Git, frontend variables, logs, or the candidate execution image. An explicitly
set empty or malformed key fails startup. A different key fails startup when
the database already contains the encrypted key-check record. There is no
fallback to local memory after opting into shared storage.

The store uses [cryptography's Fernet authenticated encryption](https://cryptography.io/en/latest/fernet/).
Only SHA-256 hashes of high-entropy opaque cookie/state identifiers are retained
as lookup keys. GitHub tokens, PKCE verifiers, user profiles, destinations and
the authenticated expiry are inside bounded encrypted envelopes. Envelopes
also bind the record kind and hashed identifier, so ciphertext cannot be moved
to another session or OAuth flow. The database contains ciphertext, kind,
hashed ID and indexed expiry metadata; Fernet also exposes creation timestamps.
Database encryption does not protect a compromised API process holding the key.

OAuth flows expire after 10 minutes and are consumed with a single transactional
`DELETE ... RETURNING`. At most one worker receives the PKCE verifier, including
under concurrent callbacks. Sessions expire after at most eight hours or the
shorter GitHub token lifetime. Both the indexed expiry and encrypted payload
expiry are checked. Extending the plaintext database expiry cannot extend a
session. Logout and GitHub 401 responses revoke the shared record. Opaque
session identifiers rotate after each successful sign-in.

Expired rows are pruned when new records are written. Writes serialize capacity
decisions and enforce 1,000 pending OAuth flows and 10,000 sessions per database.
Request admission limits apply separately to OAuth login and verification/job
mutations. Database outages return an unavailable response and do not activate
a local fallback. As with ordinary session revocation, an already authorized
in-flight request may finish after logout.

Replacing the key requires a deliberate operator migration or a maintenance
reset of the auth tables, which invalidates all sessions and pending sign-ins.
Automatic key rotation is not implemented. Keep database backups and the key
under separate access controls. Losing the key requires users to sign in again
after the store is reset. The key-check table prevents accidental mixed-key
deployments; it is not a key recovery mechanism.

Tests cover SQLite cross-instance reads and revocation, concurrent single-use
flow consumption, bounded payloads, ciphertext tampering/swapping, authenticated
expiry, wrong keys, API restart persistence, and optional PostgreSQL concurrency.
`pytest tests/test_auth_store.py` runs local tests. Set
`CODEHOUND_TEST_POSTGRES_URL` to a dedicated PostgreSQL test database for the
integration case; it creates and removes its own random schema.
