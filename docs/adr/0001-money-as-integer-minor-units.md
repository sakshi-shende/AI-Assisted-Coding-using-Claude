# 1. Money as integer minor units

## Status

Accepted

## Context

ExpenseFlow stores and compares monetary amounts (`amount_minor`, `amount_base_minor`)
at every stage of the one journey it supports: submission, base-currency normalisation,
and approval/rejection. These values need to be exact — arithmetic on them must be
reproducible and auditable, since they persist as the system of record for what was
submitted and what was approved.

## Decision

Store every monetary amount as an integer number of the currency's minor units (e.g.
paise for INR, cents for USD), never as a float or a native `DECIMAL` type. This is
codified as a project-wide convention in `CLAUDE.md` ("Money is stored as integer
minor units, never float") and enforced in `app/models.py` (`Integer` columns) and
`app/schemas.py` (`amount_minor: int`).

## Alternatives considered

- **Float (`float`/`double`).** Rejected outright: binary floating point cannot
  represent most decimal fractions exactly (e.g. `0.1 + 0.2 != 0.3`), so repeated
  addition or FX conversion would silently drift. Unacceptable for a value that gets
  audited and approved.
- **`Decimal` stored as a `NUMERIC`/`DECIMAL` column.** More precise than float, but
  adds complexity this PoC doesn't need: SQLite has no native decimal type (SQLAlchemy
  would store it as text or a float-backed affinity depending on configuration),
  `Decimal` isn't natively JSON-serialisable (FastAPI/pydantic would need to convert it
  to a string or float at the API boundary anyway, which reopens the precision
  question), and it's heavier to reason about across the FX-conversion math than plain
  integers.
- **Integer minor units.** Chosen. Every amount is an exact integer; addition,
  comparison, and (future) FX-ratio conversion are exact integer operations with no
  representation error. It serialises as a plain JSON number with no special handling,
  and matches common practice in payment systems (e.g. Stripe's minor-unit amounts).

## Consequences

- All arithmetic on `amount_minor` / `amount_base_minor` is exact and reproducible —
  no rounding drift from repeated operations.
- Every boundary that displays or accepts a human-readable amount (the Streamlit UI,
  any future report) must convert explicitly, and that conversion is a manual
  convention rather than something the type system enforces. `ui/app.py` does this
  today via `_format_amount` / `_format_rupees`, both dividing by 100.
- That divide-by-100 assumption is currently hardcoded for two-decimal currencies. It
  is incorrect for currencies with a different number of minor-unit digits (e.g. JPY,
  which has none) — not a problem yet since the API only validates a 3-letter currency
  code and doesn't special-case decimal places, but worth flagging if a
  non-two-decimal currency is ever exercised.
- The same "no float" philosophy is intended to extend to FX rates (stored as an
  integer numerator/denominator ratio per `docs/ARCHITECTURE.md`), so that a
  conversion computed today can be reproduced exactly later. That FX storage isn't
  exercised yet since real FX conversion isn't implemented (see `docs/HANDOFF.md`).
