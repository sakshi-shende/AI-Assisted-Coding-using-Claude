# ExpenseFlow Architecture

PoC expense submission and approval API. One user journey: submit an expense, convert it to base currency (INR), approve or reject it. See `CLAUDE.md` for stack and conventions this design follows.

## 1. SQLite Schema — `expenses` table

| Column | Type | Reason |
|---|---|---|
| `id` | `Integer`, PK, autoincrement | Stable identifier used in all endpoint paths. |
| `submitted_by` | `String`, not null | Who submitted the expense — free-text identifier (email/username), no auth system in scope. |
| `original_amount_minor` | `Integer`, not null | Amount as entered, in integer minor units of the *original* currency — never float. |
| `original_currency` | `String(3)`, not null | ISO-4217 code of the submitted amount; needed to know what the FX rate converted *from*, and for audit. |
| `base_amount_minor` | `Integer`, not null | INR-normalized amount in paise, computed once at submission time (write-time normalization per spec). This is what approvers reason about. |
| `fx_rate_numerator` | `Integer`, not null | Rate stored as an exact integer ratio rather than a float, so the conversion used is reproducible/auditable without ever persisting a lossy float for a money-adjacent value. |
| `fx_rate_denominator` | `Integer`, not null, default `1` | Paired with numerator; e.g. rate `83.1245` → scaled to a fixed-precision integer pair. |
| `status` | `String`, not null, default `"pending"`, `CheckConstraint IN ('pending','approved','rejected')` | Status lifecycle. The CHECK constraint is a DB-level backstop against invalid *values*; the transition guard (section 4c) is what prevents invalid *transitions*. |
| `reviewed_by` | `String`, nullable | Who approved/rejected; null while pending. |
| `created_at` | `DateTime`, not null, server default `now()` | Submission audit timestamp. |
| `updated_at` | `DateTime`, not null, default/`onupdate` `now()` | Generic last-touched timestamp for PoC debugging. |
| `reviewed_at` | `DateTime`, nullable | Set only on approve/reject — distinguishes "never reviewed" from "reviewed long ago." |

`base_amount_minor` stays an `Integer` at rest; the only int/float boundary crossing happens transiently in Python at submission time (see 4b), never at storage.

## 2. Endpoints

The approver needs to see pending items to decide on them, so the two GET endpoints are load-bearing for the journey, not scope creep — "approve or reject it" is impossible to fulfill without a way to look an expense up first.

**Decision: two POST action endpoints (`/approve`, `/reject`) instead of one generic PATCH.** A `PATCH {status: "..."}` lets a client send an arbitrary or already-past target state, pushing validation into the handler. Two narrow endpoints each encode exactly one legal transition in the route itself — there's no request body that can express an invalid target state, because the target isn't a field.

| # | Method & Path | Request Body | Response Body | Purpose |
|---|---|---|---|---|
| 1 | `POST /expenses` | `{submitted_by, original_amount_minor, original_currency}` | Full expense object, `status=pending` | Submit; synchronously fetches FX rate and computes `base_amount_minor` before insert. |
| 2 | `GET /expenses` | query: `status?` | List of expense objects | Approver views pending items (`?status=pending`); minimum needed to drive the review half of the journey. |
| 3 | `GET /expenses/{id}` | — | Single expense object | Inspect one expense before deciding — needed since approve/reject take no descriptive body. |
| 4 | `POST /expenses/{id}/approve` | `{reviewed_by}` | Updated expense, `status=approved` | Approve a pending expense; `409` if not currently pending. |
| 5 | `POST /expenses/{id}/reject` | `{reviewed_by}` | Updated expense, `status=rejected` | Reject a pending expense; `409` if not currently pending. |

No auth, no multi-currency reporting, no PUT/DELETE — nothing beyond submit/view/decide.

## 3. File Layout (exactly the 5 required files)

- **`app/main.py`** — `FastAPI()` app instance, `load_dotenv()` at startup, calls `db.init_db()` on startup, includes the router from `routes.py`.
- **`app/db.py`** — SQLAlchemy `engine` (SQLite, `expenseflow.db`, `connect_args={"check_same_thread": False}`), `SessionLocal`, declarative `Base`, `init_db()`, and a `get_db()` FastAPI dependency (commit/rollback/close per request).
- **`app/models.py`** — the single `Expense` ORM class mapping to the schema in section 1, including the `CheckConstraint`.
- **`app/schemas.py`** — pydantic v2 models: `ExpenseCreate`, `ExpenseOut` (`ConfigDict(from_attributes=True)`), `ApprovalRequest`.
- **`app/routes.py`** — `APIRouter` with all 5 handlers, plus the httpx FX-fetch helper, the rounding/conversion helper, and the conditional-UPDATE transition guard.

**Friction, called out honestly:** the 5-file constraint pushes the httpx FX client, the conversion/rounding math, and the transition-guard logic all into `routes.py`, mixing HTTP-layer concerns with domain logic. Acceptable for a PoC if kept organized as clearly-named private helpers (`_fetch_fx_rate`, `_convert_to_base`) placed above the route handlers, each fully type-hinted. No 6th file is proposed.

## 4. Edge Cases

**(a) FX API call fails or times out during submission.**
Normalization is synchronous and `base_amount_minor` is `NOT NULL` with no valid placeholder — so there's no "save as pending-unconverted" escape hatch. Order of operations: fetch rate → compute base amount → only then `INSERT`. The `httpx` client uses an explicit timeout (e.g. `httpx.Timeout(5.0)`, sourced from an env var like `FX_API_TIMEOUT_SECONDS` via `python-dotenv`, not hardcoded). No retry loop (would need a new dependency or hand-rolled backoff — out of scope for a PoC). On timeout/connection error/non-2xx, return `502 Bad Gateway` with no row written — the client can safely resubmit.

**(b) Rounding/precision loss converting integer minor units through a float FX rate.**
Represent the rate as an exact integer ratio at ingestion (`Decimal(str(rate))`, never `Decimal(float)`, scaled to `(numerator, denominator)`), then compute `base_amount_minor` with pure integer arithmetic and a fixed round-half-up rule: `(original_amount_minor * numerator + denominator // 2) // denominator`. This is deterministic and reproducible later from the stored ratio alone, satisfying the audit need without ever letting a float touch the persisted amount.

**(c) Concurrent/duplicate approve or reject requests on the same expense.**
A "SELECT, check status in Python, then UPDATE" is a TOCTOU race under concurrent requests. Instead, perform the transition as a single conditional UPDATE with the guard in the `WHERE` clause: `UPDATE expenses SET status='approved', reviewed_by=:who, reviewed_at=:now WHERE id=:id AND status='pending'`, then check `rowcount`. `rowcount == 0` → `409 Conflict` (already decided or missing); `rowcount == 1` → commit and `200`. The atomicity of the single statement's WHERE-evaluate-and-write is what prevents the race — not an app-level `if`.
