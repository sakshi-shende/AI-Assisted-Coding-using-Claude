# ExpenseFlow — Handoff

## What it does

ExpenseFlow is a small PoC API for one user journey: submit an expense, normalise it
to a base currency, then approve or reject it. A Streamlit UI (`ui/app.py`) drives the
API for manual testing, and a `/reports/insights` endpoint asks Claude for a short
natural-language summary of all expenses.

See `README.md` for the full endpoint reference, setup, and run instructions — this
document does not repeat those, only what a deployment engineer needs to know beyond
them.

## How it works

- `app/main.py` builds the FastAPI app, loads `.env`, and on startup calls
  `db.init_db()`, which runs `Base.metadata.create_all()` — the SQLite file and its
  tables are created automatically if they don't exist. There are no migrations
  (no Alembic or equivalent); schema changes require manually altering or recreating
  `expenseflow.db`.
- Storage is a single SQLite file (`expenseflow.db`) opened with
  `check_same_thread=False`. There's no connection pooling story beyond SQLAlchemy's
  default — fine for a PoC, not built for concurrent-writer load.
- `POST /expenses` writes a row synchronously; `GET /expenses` supports optional
  `status` and `category` filters; `GET /expenses/{id}` fetches one row;
  `POST /expenses/{id}/approve` and `/reject` perform the status transition as a
  single conditional `UPDATE ... WHERE status='pending'` so concurrent approve/reject
  calls can't race each other (see `app/routes.py::_transition`). Don't refactor this
  into a read-then-write pattern — that would reintroduce the race it was written to
  avoid.
- `GET /reports/insights` calls Anthropic's Claude API synchronously, once per
  request, with all expenses summarized into the prompt. There's no caching, so every
  call costs money and adds Claude's round-trip latency. If the call fails, times out,
  or returns something that isn't valid JSON in the expected shape (after one retry),
  the endpoint returns a safe fallback (`{"summary": "Insights are temporarily
  unavailable. Please try again later.", "bullets": []}`) instead of erroring.

## Known implementation gaps

- **FX conversion is not implemented.** `POST /expenses` sets `amount_base_minor` as a
  straight 1:1 copy of `amount_minor` (`fx_rate_numerator=1`, `fx_rate_denominator=1`).
  There's a `TODO` in `app/routes.py` marking this. Do not assume currency conversion
  is happening — a 100 USD expense and a 100 INR expense currently both normalise to
  "100" base units.
- `docs/ARCHITECTURE.md` describes a design (different column names like
  `original_amount_minor`, a synchronous FX fetch step, `status`-only filtering) that
  does not match the current code in several places. Treat `app/*.py` as ground truth,
  not that document.
- There is no `tests/` directory — `python -m pytest -q` collects zero tests. There is
  no CI configuration in the repo.

## What a deployment engineer needs to know

- **Run command as documented (`uvicorn app.main:app --reload`) is dev-only.** `--reload`
  should not be used in any deployed environment. Run plain `uvicorn app.main:app` (or
  behind a process manager) instead, and decide on a worker count with SQLite's
  single-writer limitations in mind — it's not built to scale past light, low-concurrency
  use.
- **Secrets:** the only secret currently in use is `ANTHROPIC_API_KEY`, read via
  `python-dotenv` in `app/insights.py`. There is **no `.gitignore` in this repo**, and
  `.env` (containing the real key) is currently untracked but not excluded from git.
  Add a `.gitignore` covering `.env`, `.venv/`, `__pycache__/`, and `expenseflow.db`
  before this repo is pushed anywhere, to avoid accidentally committing the key or the
  data file.
- **No authentication or authorization** on any endpoint — anyone who can reach the
  service can submit, list, approve, or reject expenses. This is acceptable for a PoC
  per `CLAUDE.md` but the service must not be exposed publicly as-is.
- **No CORS middleware** is configured in `app/main.py`. The current Streamlit UI calls
  the API server-side via `httpx`, so this hasn't mattered yet — but a future
  browser-based frontend served from a different origin would need
  `CORSMiddleware` added.
- **Port defaults to 8000.** On a shared host, check that port isn't already bound by
  another process/user before starting the server; the API and the Streamlit UI both
  need their own free ports, and the UI's `API_BASE` env var must point at whichever
  port the API actually bound to.
- **Data durability:** `expenseflow.db` is a single file relative to the process's
  working directory. If this is deployed in a container or ephemeral environment, that
  file needs a persistent volume or it will be lost on restart.
