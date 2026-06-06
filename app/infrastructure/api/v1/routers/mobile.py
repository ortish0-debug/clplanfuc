"""Mobile API gateway (Sprint 25)."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.finance import Account, Transaction, TransactionStatus, TransactionType
from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db

router = APIRouter(
    prefix="/mobile",
    tags=["Mobile API"],
)


@router.get("/dashboard/stats", status_code=200)
async def get_mobile_stats(
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
):
    """Compact aggregated dashboard stats for mobile (minimal payload)."""
    from app.domain.models.finance import Company

    result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = result.scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Get account balances
    acc_result = await db.execute(
        select(Account).where(
            and_(
                Account.company_id == company.id,
                Account.is_deleted.is_(False),
            )
        )
    )
    accounts = acc_result.scalars().all()
    total_balance = sum(acc.current_balance for acc in accounts)

    # Get month stats
    month_ago = datetime.now(tz=timezone.utc) - timedelta(days=30)
    txn_result = await db.execute(
        select(
            func.sum(Transaction.amount_base_currency).label("income"),
            func.sum(Transaction.amount_base_currency).label("expense"),
        ).where(
            and_(
                Transaction.company_id == company.id,
                Transaction.created_at >= month_ago,
            )
        )
    )
    row = txn_result.one()
    income = float(row.income or 0)
    expense = float(row.expense or 0)

    return {
        "balance_rub": float(total_balance),
        "income_month": income,
        "expense_month": expense,
        "accounts_count": len(accounts),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


class BiometricAuthRequest(BaseModel):
    """Biometric token for mobile auth."""

    biometric_token: str


@router.post("/auth/biometric", status_code=200)
async def auth_biometric(
    body: BiometricAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Biometric login (FaceID/Fingerprint) -> JWT token (stub)."""
    if not body.biometric_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing biometric_token",
        )

    # Stub: validate biometric token
    # Production: integrate with device verification API
    user_id = "biometric_verified_user"

    # Generate JWT (stub)
    jwt_token = f"mobile_jwt_{body.biometric_token[:16]}"

    return {
        "status": "success",
        "access_token": jwt_token,
        "token_type": "bearer",
        "user_id": user_id,
    }
