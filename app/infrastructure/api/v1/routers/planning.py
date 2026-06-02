"""Planning and import API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.planning import PlannedTransaction
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard, CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.import_service import parse_csv_transactions

router = APIRouter(prefix="/companies/{company_id}/planning", tags=["Planning"])


class ImportCSVRequest(BaseModel):
    csv_content: str


@router.get("/calendar", status_code=200)
async def get_payment_calendar(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> list:
    """Get unexecuted planned transactions."""
    result = await db.execute(
        select(PlannedTransaction).where(
            PlannedTransaction.company_id == company_id,
            PlannedTransaction.is_executed == False,
        )
    )
    transactions = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "plan_date": str(t.plan_date),
            "amount": float(t.amount),
            "type": t.transaction_type,
            "description": t.description,
        }
        for t in transactions
    ]


@router.post("/import-csv", status_code=201)
async def import_csv_transactions(
    company_id: UUID,
    request: ImportCSVRequest,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Import transactions from CSV."""
    transactions = parse_csv_transactions(request.csv_content)

    for t in transactions:
        planned = PlannedTransaction(
            company_id=company_id,
            account_id=current_user.company_id,
            amount=t['amount'],
            transaction_type=t['transaction_type'],
            plan_date=t['plan_date'],
            description=t['description'],
        )
        db.add(planned)

    await db.commit()
    return {"status": "success", "imported": len(transactions)}
