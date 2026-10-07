# Shared request limits

CodeHound limits costly request attempts using database-backed fixed windows.
Verification creation, GitHub evidence intake, and execution creation share one
budget per authenticated GitHub owner. OAuth initiation has a separate budget per
remote client IP. These counters are independent of execution queue capacity.

Defaults and strict environment settings:

| Setting | Default | Allowed values |
| --- | --- | --- |
| `CODEHOUND_MUTATION_REQUEST_LIMIT` | 120 | 1–10,000 requests |
| `CODEHOUND_OAUTH_REQUEST_LIMIT` | 20 | 1–1,000 requests |
| `CODEHOUND_REQUEST_LIMIT_WINDOW_SECONDS` | 60 | 1–3,600 seconds |
| `CODEHOUND_REQUEST_LIMIT_MAX_BUCKETS` | 100,000 | 1–1,000,000 stored principal/bucket rows |

Values must be decimal integers without signs, whitespace, or leading zeros.
Invalid settings fail startup validation; request enforcement also fails closed
with HTTP 503. Counter storage exhaustion or database failures return HTTP 503.
An exhausted request budget returns HTTP 429 and an integer `Retry-After` telling
the client how many seconds remain until the next window.

Windows start at Unix time multiples of the configured duration. The budget
resets at each boundary, so requests close to a boundary can consume two windows'
allowances in a short interval. This is a fixed-window limiter, not a precise
sliding-window traffic guarantee. Instances sharing the same PostgreSQL or SQLite
database share counters. Application host clocks should remain synchronized.

Every request reaching the limiter consumes one attempt, including idempotent
retries and requests that encounter an upstream failure, queue limit, or conflict
after admission. Idempotency still prevents duplicate records or executions; it
does not bypass request throttling. Authentication, same-origin checks, and body
validation precede admission. Execution creation also validates the owner-scoped
record and operator configuration before admission. Intake charges before its
record lookup, so a missing intake record still consumes an attempt. Read
requests and cancellation do not consume this mutation budget.

OAuth limits use the ASGI request's remote peer address. The application ignores
`X-Forwarded-For` and `Forwarded` headers. The supplied Docker command and local
startup instructions explicitly pass Uvicorn `--no-proxy-headers`: Uvicorn's
default forwarding middleware trusts loopback peers and can replace the ASGI
client address with an arbitrary `X-Forwarded-For` supplied by a direct local
client or forwarded through the development proxy. Starting Uvicorn without
this flag can therefore bypass per-peer OAuth limits.

Behind a reverse proxy, clients share the proxy's budget with forwarding
disabled. Recovering individual addresses requires a deliberate deployment
configuration that only trusts the actual proxy and ensures it replaces or
sanitizes incoming forwarding headers. Never trust forwarding headers from
arbitrary clients. This version has no application-level proxy trust
configuration. Missing or invalid peer addresses share an `unknown` bucket.

Stored identities are SHA-256 hashes salted with the database's stable execution
namespace. Raw owner IDs and IP addresses are absent from this table. Hashing is
not encryption or an anonymity guarantee. Transactions serialize counter
admission and the global row cap across PostgreSQL connections and SQLite
processes. At most 200 expired rows are pruned per request. Active principal rows
are reused across windows; the configured cap bounds this counter table even
when many remote identities attempt OAuth initiation.

These limits reduce request abuse and bound the counter table. They do not bound
all retained verification snapshots, execution artifacts, authentication state,
or total database size, and do not establish a distributed storage quota.
