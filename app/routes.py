"""HTTP route handlers for ExpenseFlow."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.insights import generate_insight
from app.models import Expense
from app.schemas import ApprovalRequest, ExpenseCreate, ExpenseOut

router = APIRouter()


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(payload: ExpenseCreate, db: Session = Depends(get_db)) -> Expense:
    """Submit a new expense, normalised to base currency, with status `pending`."""
    # TODO: fetch the real FX rate and compute amount_base_minor from it.
    amount_base_minor = payload.amount_minor
    expense = Expense(
        submitted_by=payload.submitted_by,
        description=payload.description,
        category=payload.category,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        amount_base_minor=amount_base_minor,
        fx_rate_numerator=1,
        fx_rate_denominator=1,
        status="pending",
    )
    db.add(expense)
    db.flush()
    db.refresh(expense)
    return expense


@router.get("/expenses", response_model=list[ExpenseOut])
def list_expenses(
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Expense]:
    """List expenses, optionally filtered by status and/or category."""
    stmt = select(Expense)
    if status is not None:
        stmt = stmt.where(Expense.status == status)
    if category is not None:
        stmt = stmt.where(Expense.category == category)
    return list(db.execute(stmt).scalars().all())


@router.get("/reports/insights")
def get_insights(db: Session = Depends(get_db)) -> dict[str, object]:
    """Generate short spending insights across all expenses."""
    expenses = list(db.execute(select(Expense)).scalars().all())
    expense_dicts = [
        {
            "amount_base_minor": expense.amount_base_minor,
            "category": expense.category,
            "status": expense.status,
        }
        for expense in expenses
    ]
    return {"insight": generate_insight(expense_dicts)}


@router.get("/expenses/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Fetch a single expense by id."""
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseOut)
def approve_expense(
    expense_id: int, payload: ApprovalRequest, db: Session = Depends(get_db)
) -> Expense:
    """Approve a pending expense; 409 if it is not currently pending."""
    return _transition(db, expense_id, payload.reviewed_by, "approved")


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseOut)
def reject_expense(
    expense_id: int, payload: ApprovalRequest, db: Session = Depends(get_db)
) -> Expense:
    """Reject a pending expense; 409 if it is not currently pending."""
    return _transition(db, expense_id, payload.reviewed_by, "rejected")


def _transition(db: Session, expense_id: int, reviewed_by: str, new_status: str) -> Expense:
    """Move a pending expense to `new_status` via a single conditional UPDATE.

    The pending-status check lives in the WHERE clause so the check-and-set is
    atomic under concurrent approve/reject calls, per the race documented in
    ARCHITECTURE.md 4c.
    """
    stmt = (
        update(Expense)
        .where(Expense.id == expense_id, Expense.status == "pending")
        .values(status=new_status, reviewed_by=reviewed_by, reviewed_at=datetime.utcnow())
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Expense is not pending or does not exist")
    db.flush()
    expense = db.get(Expense, expense_id)
    db.refresh(expense)
    return expense
