# ExpenseFlow

A small expense submission and approval API (PoC, not production). One user journey:
submit an expense, normalise it to a base currency, then approve or reject it. A
Streamlit UI in `ui/app.py` drives the API for manual testing, and a `/reports/insights`
endpoint asks Claude for a short natural-language summary of all expenses.

> **Current limitation:** `POST /expenses` does not yet fetch a real FX rate. It stores
> `amount_base_minor` as a 1:1 copy of `amount_minor` with `fx_rate_numerator=1` /
> `fx_rate_denominator=1` — see the `TODO` in `app/routes.py`. Base currency is intended
> to be INR (per `CLAUDE.md`), but no currency conversion happens yet.

## Stack

- Python (developed against 3.10.12 in this workspace; `CLAUDE.md` targets 3.12 — either works with the current code)
- FastAPI + Uvicorn
- SQLAlchemy ORM on SQLite (`expenseflow.db`)
- httpx (used by the Streamlit UI to call the API; not currently used server-side for FX)
- Pydantic v2 request/response models
- pytest
- python-dotenv for environment variables
- `anthropic` (Claude) for `/reports/insights`
- Streamlit + pandas for the UI (`ui/app.py`)

## Project layout

```
app/
  main.py      # FastAPI app, startup hook that creates tables
  db.py        # engine, session factory, get_db() dependency
  models.py    # Expense ORM model
  schemas.py   # ExpenseCreate, ExpenseOut, ApprovalRequest (pydantic v2)
  routes.py    # all HTTP route handlers
  insights.py  # Claude-backed spending insight generator
ui/
  app.py       # Streamlit front end
docs/
  ARCHITECTURE.md
expenseflow.db # SQLite file, created automatically on first run
```

## Setup on Windows

1. Install Python (3.10 or newer) from [python.org](https://www.python.org/downloads/windows/) and make sure "Add python.exe to PATH" is checked during install.
2. Open PowerShell (or Command Prompt) in the project folder.
3. Create the virtual environment:
   ```powershell
   python -m venv .venv
   ```
4. Activate it:
   - PowerShell:
     ```powershell
     .venv\Scripts\Activate.ps1
     ```
     If you get an execution-policy error, run this once first:
     ```powershell
     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
     ```
   - Command Prompt:
     ```cmd
     .venv\Scripts\activate.bat
     ```
5. Install dependencies (there is no committed `requirements.txt` yet, so install directly):
   ```powershell
   pip install fastapi "uvicorn[standard]" sqlalchemy httpx pydantic python-dotenv pytest anthropic streamlit pandas
   ```

On macOS/Linux the only difference is activation: `source .venv/bin/activate`.

## Configure `.env`

Create a `.env` file in the project root (never commit it — see `CLAUDE.md`). The
code currently reads these two variables:

| Variable | Read by | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `app/insights.py` | Claude API key used by `GET /reports/insights`. Without it, insight generation will fail and the endpoint falls back to a safe placeholder response. |
| `API_BASE` | `ui/app.py` | Base URL the Streamlit UI uses to call the API. Defaults to `http://127.0.0.1:8001` if not set. |

Example `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
API_BASE=http://127.0.0.1:8000
```

## Run the server

```
python -m uvicorn app.main:app --reload
```

This starts the API on `http://127.0.0.1:8000` and creates `expenseflow.db` and its
tables automatically on startup if they don't already exist. Use `--port <n>` if 8000
is already in use, and set `API_BASE` accordingly for the UI.

## Run the UI

```
streamlit run ui/app.py
```

Make sure `API_BASE` (env var or `.env`) points at wherever the API is actually running.

## Run tests

```
python -m pytest -q
```

Note: there is currently no `tests/` directory or test files in the repo, so this will
report "no tests ran" until tests are added.

## Endpoint reference

All request/response bodies are JSON. Money fields are integer minor units (e.g. paise),
never floats.

### `ExpenseOut` (common response shape)

| Field | Type |
|---|---|
| `id` | int |
| `submitted_by` | string |
| `description` | string |
| `category` | string |
| `amount_minor` | int |
| `currency` | string (3-letter ISO-4217) |
| `amount_base_minor` | int |
| `fx_rate_numerator` | int |
| `fx_rate_denominator` | int |
| `status` | `"pending"` \| `"approved"` \| `"rejected"` |
| `reviewed_by` | string or null |
| `created_at` | datetime |
| `updated_at` | datetime |
| `reviewed_at` | datetime or null |

### `POST /expenses`

Submit a new expense. Status is always created as `pending`.

Request body:

```json
{
  "description": "Taxi",
  "amount_minor": 4599,
  "currency": "USD",
  "category": "travel",
  "submitted_by": "jdoe"
}
```

- `description`, `category`, `submitted_by`: non-empty strings.
- `amount_minor`: integer, must be > 0.
- `currency`: exactly 3 letters, normalised to uppercase.

Response: `201` with an `ExpenseOut` object.

### `GET /expenses`

List expenses, optionally filtered.

Query parameters (both optional): `status`, `category`.

Response: `200` with a list of `ExpenseOut` objects.

### `GET /expenses/{expense_id}`

Fetch a single expense.

Response: `200` with an `ExpenseOut` object, or `404` (`{"detail": "Expense not found"}`) if it doesn't exist.

### `POST /expenses/{expense_id}/approve`

Approve a pending expense.

Request body:

```json
{ "reviewed_by": "manager@example.com" }
```

Response: `200` with the updated `ExpenseOut` (`status="approved"`, `reviewed_by` and `reviewed_at` set), or `409` (`{"detail": "Expense is not pending or does not exist"}`) if the expense is missing or not currently pending.

### `POST /expenses/{expense_id}/reject`

Same as approve, but sets `status="rejected"`. Same request body, same `409` condition.

### `GET /reports/insights`

Generate a short spending summary across all expenses via Claude.

Response: `200` with:

```json
{
  "insight": {
    "summary": "One short sentence summarizing spending.",
    "bullets": ["insight one", "insight two", "insight three"]
  }
}
```

If the Claude call fails or its response can't be parsed as valid JSON after one retry,
`insight` falls back to:

```json
{ "summary": "Insights are temporarily unavailable. Please try again later.", "bullets": [] }
```
