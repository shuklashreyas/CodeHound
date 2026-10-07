"""Optional authenticated encryption for shared, expiring server-side auth state."""

import hashlib
import json
import math
import re
import time
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from codehound.db.models import AuthRecord, AuthStoreKey
from codehound.execution.protocol import load_evidence

MAX_PAYLOAD_BYTES = 32768
KEY_SENTINEL = b"codehound-shared-auth-store-v1"
CAPACITY = {"flow": 1000, "session": 10000}
LIFETIMES = {"flow": 600, "session": 28800}


def lookup_id(identifier):
    if (
        not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 256
        or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier)
    ):
        return None
    return hashlib.sha256(identifier.encode("ascii")).hexdigest()


def valid_payload(kind, payload):
    if not isinstance(payload, dict):
        return False
    expires = payload.get("expires")
    if type(expires) not in (int, float) or not 0 < expires <= 2**53 or not math.isfinite(expires):
        return False
    if kind == "flow":
        if set(payload) != {"verifier", "expires", "verification"}:
            return False
        verifier = payload["verifier"]
        if not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", verifier):
            return False
        destination = payload["verification"]
        if destination is not None:
            try:
                if not isinstance(destination, str) or str(UUID(destination)) != destination:
                    return False
            except ValueError:
                return False
        return True
    if kind != "session" or set(payload) != {"token", "user", "expires"}:
        return False
    token, user = payload["token"], payload["user"]
    if (
        not isinstance(token, str)
        or not 0 < len(token) <= 16384
        or not isinstance(user, dict)
        or set(user) != {"id", "login", "name"}
        or type(user["id"]) is not int
        or not 0 < user["id"] < 2**63
    ):
        return False
    return all(
        isinstance(user[field], str) and 0 < len(user[field]) <= limit
        for field, limit in (("login", 100), ("name", 1024))
    )


class MemoryAuthStore:
    """Compatibility development store; intentionally limited to one API process."""

    def __init__(self, sessions, flows):
        self.sessions, self.flows = sessions, flows

    def prune(self):
        now = time.time()
        for store in (self.sessions, self.flows):
            for identifier in list(store):
                value = store.get(identifier)
                if value and value["expires"] <= now:
                    store.pop(identifier, None)

    def save_flow(self, identifier, payload):
        self.prune()
        if len(self.flows) >= CAPACITY["flow"]:
            return False
        self.flows[identifier] = payload
        return True

    def consume_flow(self, identifier):
        self.prune()
        return self.flows.pop(identifier, None)

    def save_session(self, identifier, payload):
        self.prune()
        if len(self.sessions) >= CAPACITY["session"]:
            return False
        self.sessions[identifier] = payload
        return True

    def get_session(self, identifier):
        self.prune()
        return self.sessions.get(identifier)

    def revoke_session(self, identifier):
        self.sessions.pop(identifier, None)


class SharedAuthStore:
    def __init__(self, database, key):
        try:
            # An explicitly configured empty/malformed key cannot fall back to memory.
            self.fernet = Fernet(key)
        except (ValueError, TypeError, UnicodeError):
            raise RuntimeError(
                "CODEHOUND_SESSION_ENCRYPTION_KEY must be a valid Fernet key."
            ) from None
        self.database = database
        self._check_key()

    def _check_key(self):
        table = AuthStoreKey.__table__
        try:
            with self.database.engine.begin() as connection:
                value = connection.execute(
                    select(table.c.ciphertext).where(table.c.id == 1)
                ).scalar()
                if value is None:
                    value = self.fernet.encrypt(KEY_SENTINEL).decode("ascii")
                    connection.execute(insert(table).values(id=1, ciphertext=value))
        except IntegrityError:
            # Two API workers may initialize the sentinel together. Read the winner.
            with self.database.engine.connect() as connection:
                value = connection.execute(
                    select(table.c.ciphertext).where(table.c.id == 1)
                ).scalar()
        try:
            if (
                not isinstance(value, str)
                or len(value) > 1000
                or self.fernet.decrypt(value.encode("ascii")) != KEY_SENTINEL
            ):
                raise InvalidToken
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise RuntimeError(
                "Shared session store encryption key does not match this database."
            ) from None

    def _encrypt(self, kind, identifier_hash, payload):
        raw = json.dumps(
            {"version": 1, "kind": kind, "id_hash": identifier_hash, "payload": payload},
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ValueError("Authentication payload exceeds its bounded size.")
        load_evidence(raw, limit=MAX_PAYLOAD_BYTES)
        return self.fernet.encrypt(raw).decode("ascii")

    def _decrypt(self, kind, identifier_hash, ciphertext, now):
        try:
            # Ciphertext itself is bounded before decryption and JSON parsing.
            if not isinstance(ciphertext, str) or len(ciphertext) > 50000:
                return None
            value = load_evidence(
                self.fernet.decrypt(ciphertext.encode("ascii")), limit=MAX_PAYLOAD_BYTES
            )
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "kind", "id_hash", "payload"}
                or type(value["version"]) is not int
                or value["version"] != 1
                or value["kind"] != kind
                or value["id_hash"] != identifier_hash
                or not valid_payload(kind, value["payload"])
            ):
                return None
            payload = value["payload"]
            if payload["expires"] <= now:
                return None
            return payload
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return None

    def _save(self, kind, identifier, payload):
        identifier_hash = lookup_id(identifier)
        now = time.time()
        if (
            identifier_hash is None
            or not valid_payload(kind, payload)
            or not now < payload["expires"] <= now + LIFETIMES[kind] + 1
        ):
            return False
        try:
            ciphertext = self._encrypt(kind, identifier_hash, payload)
        except (ValueError, UnicodeError):
            return False
        table = AuthRecord.__table__
        with self.database.engine.begin() as connection:
            # Row update serializes capacity decisions on PostgreSQL and SQLite.
            connection.execute(
                update(AuthStoreKey)
                .where(AuthStoreKey.id == 1)
                .values(ciphertext=AuthStoreKey.ciphertext)
            )
            connection.execute(delete(table).where(table.c.expires_ms <= int(now * 1000)))
            count = connection.execute(
                select(func.count()).select_from(table).where(table.c.kind == kind)
            ).scalar_one()
            if count >= CAPACITY[kind]:
                return False
            try:
                connection.execute(
                    insert(table).values(
                        kind=kind,
                        id_hash=identifier_hash,
                        ciphertext=ciphertext,
                        expires_ms=int(payload["expires"] * 1000),
                    )
                )
            except IntegrityError:
                # Opaque IDs are never overwritten, including on accidental reuse.
                raise ValueError("Authentication identifier is already in use.") from None
        return True

    def save_flow(self, identifier, payload):
        return self._save("flow", identifier, payload)

    def save_session(self, identifier, payload):
        return self._save("session", identifier, payload)

    def consume_flow(self, identifier):
        identifier_hash = lookup_id(identifier)
        if identifier_hash is None:
            return None
        table = AuthRecord.__table__
        # DELETE RETURNING transfers ownership to exactly one concurrent caller.
        # Expired/invalid flows are consumed too, preventing repeated exchange attempts.
        with self.database.engine.begin() as connection:
            ciphertext = connection.execute(
                delete(table)
                .where(table.c.kind == "flow", table.c.id_hash == identifier_hash)
                .returning(table.c.ciphertext)
            ).scalar()
        return (
            self._decrypt("flow", identifier_hash, ciphertext, time.time()) if ciphertext else None
        )

    def get_session(self, identifier):
        identifier_hash = lookup_id(identifier)
        if identifier_hash is None:
            return None
        now = time.time()
        table = AuthRecord.__table__
        with self.database.engine.connect() as connection:
            ciphertext = connection.execute(
                select(table.c.ciphertext).where(
                    table.c.kind == "session",
                    table.c.id_hash == identifier_hash,
                    table.c.expires_ms > int(now * 1000),
                )
            ).scalar()
        return self._decrypt("session", identifier_hash, ciphertext, now) if ciphertext else None

    def revoke_session(self, identifier):
        identifier_hash = lookup_id(identifier)
        if identifier_hash is None:
            return
        with self.database.engine.begin() as connection:
            connection.execute(
                delete(AuthRecord).where(
                    AuthRecord.kind == "session", AuthRecord.id_hash == identifier_hash
                )
            )

    def prune(self):
        with self.database.engine.begin() as connection:
            connection.execute(
                delete(AuthRecord).where(AuthRecord.expires_ms <= int(time.time() * 1000))
            )
