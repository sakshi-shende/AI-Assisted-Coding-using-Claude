"""Streamlit front end for ExpenseFlow: submit expenses, list them, and view AI insights."""

from __future__ import annotations

import os

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8001")

_STATUS_COLORS = {"approved": "#c6f6c6", "rejected": "#f6c6c6", "pending": "#fdf3c6"}
_STATUS_LABELS = {"approved": "🟢 Approved", "rejected": "🔴 Rejected", "pending": "🟡 Pending"}

st.set_page_config(page_title="ExpenseFlow", layout="wide")
st.title("ExpenseFlow")
st.caption("Submit expenses, review pending ones, and get quick spending insights.")


def _friendly_error(exc: httpx.HTTPError) -> str:
    """Turn an httpx exception into a short, human-readable message."""
    if isinstance(exc, httpx.ConnectError):
        return f"Could not connect to the API at {API_BASE}. Is it running?"
    if isinstance(exc, httpx.TimeoutException):
        return f"The API at {API_BASE} took too long to respond."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"The API returned an error: {exc.response.status_code} {exc.response.text}"
    return f"Could not reach the API at {API_BASE}: {exc}"


def _status_style(label: str) -> str:
    """Return a background-color CSS declaration for a status label cell."""
    lowered = label.lower()
    for status, color in _STATUS_COLORS.items():
        if status in lowered:
            return f"background-color: {color}"
    return ""


def _format_amount(amount_minor: int, currency: str) -> str:
    """Format integer minor units as a two-decimal amount, for display only."""
    return f"{amount_minor / 100:,.2f} {currency}"


def _format_rupees(amount_minor: int) -> str:
    """Format integer minor units as a two-decimal rupee amount, for display only."""
    return f"₹{amount_minor / 100:,.2f}"


def _review_expense(expense_id: int, action: str, reviewed_by: str) -> None:
    """POST an approve/reject decision for an expense and refresh the page."""
    if not reviewed_by:
        st.error("Enter a reviewer name before approving or rejecting.")
        return
    try:
        response = httpx.post(
            f"{API_BASE}/expenses/{expense_id}/{action}",
            json={"reviewed_by": reviewed_by},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        st.error(_friendly_error(exc))
    else:
        past_tense = "approved" if action == "approve" else "rejected"
        st.success(f"Expense #{expense_id} {past_tense}.")
        st.rerun()


if "submitting" not in st.session_state:
    st.session_state.submitting = False
if "submit_result" not in st.session_state:
    st.session_state.submit_result = None

st.header("Submit an expense")
with st.form("submit_expense"):
    submitted_by = st.text_input("Submitted by")
    amount_minor = st.number_input("Amount (minor units, e.g. paise)", min_value=1, step=1)
    currency = st.text_input("Currency (ISO-4217, e.g. INR)", max_chars=3)
    category = st.text_input("Category")
    description = st.text_input("Description")
    submit = st.form_submit_button("Submit expense", disabled=st.session_state.submitting)

if submit and not st.session_state.submitting:
    st.session_state.submitting = True
    st.session_state.submit_result = None
    st.session_state.pending_expense = {
        "submitted_by": submitted_by,
        "amount_minor": int(amount_minor),
        "currency": currency,
        "category": category,
        "description": description,
    }
    st.rerun()

if st.session_state.submitting:
    payload = st.session_state.pop("pending_expense")
    try:
        response = httpx.post(f"{API_BASE}/expenses", json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        st.session_state.submit_result = ("error", _friendly_error(exc))
    else:
        st.session_state.submit_result = ("success", f"Expense #{response.json()['id']} submitted.")
    st.session_state.submitting = False
    st.rerun()

if st.session_state.submit_result:
    kind, message = st.session_state.submit_result
    getattr(st, kind)(message)

st.header("Existing expenses")
expenses: list[dict] = []
try:
    response = httpx.get(f"{API_BASE}/expenses")
    response.raise_for_status()
except httpx.HTTPError as exc:
    st.error(_friendly_error(exc))
else:
    expenses = response.json()
    if expenses:
        table = pd.DataFrame(expenses)
        table["Amount"] = table.apply(
            lambda row: _format_amount(row["amount_minor"], row["currency"]), axis=1
        )
        table["Amount (INR)"] = table["amount_base_minor"].map(_format_rupees)
        table["Status"] = table["status"].map(lambda s: _STATUS_LABELS.get(s, s))
        display_table = table[
            [
                "id",
                "submitted_by",
                "description",
                "category",
                "Amount",
                "Amount (INR)",
                "Status",
                "reviewed_by",
                "created_at",
                "reviewed_at",
            ]
        ]
        st.dataframe(
            display_table.style.map(_status_style, subset=["Status"]), use_container_width=True
        )
    else:
        st.info("No expenses yet.")

st.header("Review expenses")
pending = [expense for expense in expenses if expense["status"] == "pending"]
if not pending:
    st.info("No pending expenses to review.")
else:
    reviewer = st.text_input("Reviewed by", key="reviewer_name")
    for expense in pending:
        cols = st.columns([4, 1, 1])
        cols[0].write(
            f"#{expense['id']} — {expense['description']} "
            f"({_format_amount(expense['amount_minor'], expense['currency'])}, {expense['category']})"
        )
        if cols[1].button("Approve", key=f"approve_{expense['id']}"):
            _review_expense(expense["id"], "approve", reviewer)
        if cols[2].button("Reject", key=f"reject_{expense['id']}"):
            _review_expense(expense["id"], "reject", reviewer)

st.header("Insights")
if st.button("Generate insights"):
    try:
        response = httpx.get(f"{API_BASE}/reports/insights")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        st.error(_friendly_error(exc))
    else:
        insight = response.json()["insight"]
        st.write(insight["summary"])
        for bullet in insight["bullets"]:
            st.markdown(f"- {bullet}")
