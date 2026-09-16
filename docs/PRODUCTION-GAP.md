# ExpenseFlow — Production Readiness Gap Audit

This audits the current code (`app/*.py`, `ui/app.py`) against a production bar. It is
based on what's actually implemented today, not on `docs/ARCHITECTURE.md`'s intended
design, which differs from the code in several places (see `docs/HANDOFF.md`).

**Classification key:** *Blocking* = should not go live to real users/data without
this. *Deferrable* = acceptable to launch without, revisit afterward.

## Summary

| # | Area | Classification | Rough effort |
|---|---|---|---|
| 1 | Authentication and key rotation | Blocking | Medium (2–4 days) |
| 2 | Input validation | Blocking | Small (0.5–1 day) |
| 3 | Rate limiting | Blocking | Small–Medium (1–2 days) |
| 4 | Observability and logging | Blocking | Medium (2–3 days) |
| 5 | Error handling | Blocking | Small–Medium (1–2 days) |
| 6 | Database migrations and pooling | Blocking (migrations) / Deferrable (pooling) | Medium (2–3 days) |
| 7 | Secrets management | Blocking (minimum hygiene) | Small now / Medium later |
| 8 | Tests and coverage | Blocking | Medium–Large (3–5 days) |
| 9 | Deployment and health checks | Blocking | Medium (2–3 days) |
| 10 | Data privacy for expense data | Blocking (access scoping) / Deferrable (encryption/retention) | Medium, bundled with #1 |

Almost everything is blocking because this is currently a no-auth, no-tests, no-ops
PoC — that's an expected state for what `CLAUDE.md` describes it as, not a defect in
how it was built for its stated purpose.

## 1. Authentication and key rotation

**Gap:** There is no authentication on any endpoint — `app/main.py` registers no auth
dependency, middleware, or security scheme. `submitted_by` and `reviewed_by` are
free-text fields the client supplies with no verification, so any caller can submit as
anyone and approve/reject anyone else's expense. There's also no authorization
(role separation between "submitter" and "approver" — the same unauthenticated caller
can do both). Separately, the only credential in the system, `ANTHROPIC_API_KEY`, has
no rotation mechanism: it's a single static value read once per `/reports/insights`
call via `os.environ.get`, and rotating it requires editing `.env` and restarting the
process.

**Classification:** Blocking.

**Effort:** Medium (2–4 days) for a first pass — e.g. API key or JWT auth, tying
`submitted_by`/`reviewed_by` to the authenticated principal instead of trusting the
request body, and a basic submitter/approver role check on the approve/reject routes.

## 2. Input validation

**Gap:** `ExpenseCreate` validates types and non-emptiness (`min_length=1`) and
normalises/validates `currency` to a 3-letter code, but: `description`, `category`,
and `submitted_by` have no `max_length`, so an arbitrarily large string is accepted
into the DB on every request; `amount_minor` has a lower bound (`gt=0`) but no upper
bound; `category` is free text with no allow-list, so values fragment silently
(`"Travel"` vs `"travel"`) and there's no validation against a known set;
`GET /expenses`'s `status`/`category` query params are typed as plain optional
strings, so a typo like `?status=Pedning` just silently returns an empty list instead
of a `400`.

**Classification:** Blocking (unbounded string/amount fields are a real resource and
data-quality risk once this is reachable by untrusted clients).

**Effort:** Small (0.5–1 day) — add `max_length`/upper bounds to the existing
`pydantic` fields and validate `status` against the same three values the `CheckConstraint`
in `models.py` already encodes.

## 3. Rate limiting

**Gap:** No rate limiting exists anywhere — no middleware, no per-IP/per-user
throttling, nothing in the dependency list (`slowapi`, `limits`, etc. are not
installed). This is most acute on `GET /reports/insights`, which makes a real,
billed Anthropic API call on every single invocation with no caching or debouncing —
an unauthenticated caller can currently drive unbounded Anthropic spend by hitting
that one endpoint in a loop.

**Classification:** Blocking (direct, uncapped cost exposure via the insights
endpoint, independent of the general DoS concern on the other routes).

**Effort:** Small–Medium (1–2 days) for basic per-IP or per-key throttling on
`/reports/insights` at minimum; broader rate limiting across all routes is the same
order of effort once the mechanism exists.

## 4. Observability and logging

**Gap:** The only logging in the entire app is three `logger.error`/`logger.warning`
calls inside `app/insights.py`, and there's no `logging.basicConfig` (or structured
logging setup) anywhere in `app/main.py`, so even those may not surface predictably
depending on how the ASGI server's logging is configured. `app/routes.py`, `app/db.py`,
and `app/main.py` have zero log statements — no record of requests, responses,
approve/reject decisions, or errors outside the insights path. There's no correlation
ID / request ID, no metrics endpoint, no tracing, and no distinction between the audit
trail in the DB (which only reflects the current row state) and an actual append-only
log of who did what and when.

**Classification:** Blocking.

**Effort:** Medium (2–3 days) for structured request logging, correlation IDs, and at
minimum an audit log line on approve/reject; metrics and tracing can be deferred past
initial launch.

## 5. Error handling

**Gap:** Only two explicit error paths exist: `404` on a missing expense and `409` on
an invalid status transition (`app/routes.py`). Everything else — a DB integrity
error, an unexpected `None`, an Anthropic SDK exception not covered by the two
`except` clauses in `insights.py::generate_insight` (which only catches
`anthropic.APIStatusError` and `anthropic.APIConnectionError`) — is unhandled and
propagates as FastAPI's generic `500`. That last point is a real, already-existing
bug: an Anthropic exception type outside those two (e.g. an auth error from a missing
key) will crash the request instead of hitting the intended fallback response. There's
no global exception handler to produce a consistent error body shape across the API.

**Classification:** Blocking.

**Effort:** Small–Medium (1–2 days) — add a global FastAPI exception handler for
uncaught exceptions, and widen or generalize the `except` clauses in `insights.py` so
the fallback path is actually reached on any Anthropic-side failure.

## 6. Database migrations and pooling

**Gap:** There is no migration tool (no Alembic, no equivalent) anywhere in the
project. `db.py::init_db()` only calls `Base.metadata.create_all()`, which creates
missing tables but never alters existing ones — any future change to an existing
column (type, nullability, adding a `NOT NULL` column to a populated table) has no
supported upgrade path and would require manually dropping/recreating
`expenseflow.db`, losing data. On pooling: the engine (`create_engine` in `db.py`) uses
SQLAlchemy's defaults with no `pool_size`/`max_overflow`/`pool_timeout` tuning; this is
largely moot while the backing store is single-writer SQLite, but there's currently no
plan or configuration for what pooling would look like if this ever moved to a
multi-connection database (e.g. Postgres).

**Classification:** Migrations: Blocking, once there is real data whose schema will
ever need to change. Pooling: Deferrable while SQLite is the store.

**Effort:** Medium (2–3 days) to introduce Alembic and write the baseline migration
capturing the current schema; pooling config is a few hours of work if/when the
backing store changes.

## 7. Secrets management

**Gap:** The only secret in use, `ANTHROPIC_API_KEY`, is loaded from a plaintext
`.env` file via `python-dotenv`. There is currently **no `.gitignore` in this repo**,
so nothing stops `.env` (with the real key) from being committed. There's no secrets
manager integration (Vault, cloud KMS/Secrets Manager, etc.), and no rotation
support — changing the key means editing `.env` and restarting the process. `.env` is
loaded independently in both `app/main.py` and `app/insights.py`, which is redundant
but not itself a security issue.

**Classification:** Blocking at minimum hygiene level (the missing `.gitignore` is an
active leak risk, already flagged in `docs/HANDOFF.md`). Full secrets-manager
integration is Deferrable until there's real infrastructure to integrate with.

**Effort:** Small (a few hours) to add `.gitignore` and confirm no secret is already
committed; Medium (1–2 days) later for a real secrets-manager integration if/when
deployed to cloud infrastructure.

## 8. Tests and coverage

**Gap:** There are zero test files anywhere in the repository — no `tests/`
directory exists, and `python -m pytest -q` currently collects nothing. `CLAUDE.md`
documents a test command that does nothing today. There's no coverage tooling
(`pytest-cov`/`coverage.py`) and no CI configuration to run tests automatically.

**Classification:** Blocking.

**Effort:** Medium–Large (3–5 days) for a first meaningful suite: the six endpoints,
the pending→approved/rejected transition guard (including the concurrent-request race
it's designed to prevent), validation edge cases, and the insights fallback path.
Coverage tooling and CI wiring are incremental on top of that.

## 9. Deployment and health checks

**Gap:** No health or readiness endpoint exists (no `/health`, `/ping`, or similar
route) for a load balancer or orchestrator to probe. The documented run command
(`uvicorn app.main:app --reload`) uses dev-only autoreload, which is unsuitable for
production (a file touch would restart the worker mid-traffic). There's no Dockerfile,
no container image, no process manager/systemd unit, and no CI/CD pipeline anywhere in
the repo. There's also no environment-based configuration split (dev vs. prod runs the
identical code path).

**Classification:** Blocking.

**Effort:** Medium (2–3 days) for a minimal production path: a health endpoint, a
non-`--reload` start command, a Dockerfile, and basic environment configuration
separation.

## 10. Data privacy for expense data

**Gap:** `GET /expenses` returns every expense from every submitter to any caller —
there is no scoping (a submitter seeing only their own expenses, or an approver seeing
only what's pending), which compounds the missing-auth gap in #1. There's no data
retention or deletion policy, and no encryption at rest for `expenseflow.db` (a plain
SQLite file on disk holding `submitted_by`, `reviewed_by`, and free-text
`description`). On the positive side: the `/reports/insights` prompt
(`insights.py::_build_summary`) only sends `amount_base_minor`, `category`, and
`status` to Anthropic — it does not send `description` or `submitted_by`, so no
free-text or identity data currently leaves the system via that path.

**Classification:** Access scoping: Blocking (ties directly to #1). Encryption at
rest and a formal retention/deletion policy: Deferrable until there's a concrete
compliance requirement driving them.

**Effort:** Medium, largely bundled with the auth work in #1 (roughly 1–2 additional
days for access scoping once authentication exists). Encryption at rest and retention
tooling are Medium–Large depending on the actual requirement, and shouldn't be built
speculatively before one exists.
