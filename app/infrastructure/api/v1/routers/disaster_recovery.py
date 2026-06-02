"""Disaster recovery and backup API."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance
from app.infrastructure.database.session import get_db
from app.services.backup_service import (
    create_full_backup,
    restore_from_backup,
    get_backup_history,
    get_recovery_point_objective,
)

router = APIRouter(prefix="/companies/{company_id}/disaster-recovery", tags=["Disaster Recovery"])


@router.post("/backup/create", status_code=201)
async def create_backup(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create full database backup for company."""
    return await create_full_backup(db, company_id)


@router.post("/backup/restore", status_code=200)
async def restore_backup(
    company_id: UUID,
    backup_id: str = Query(...),
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Restore database from backup."""
    return await restore_from_backup(db, backup_id, company_id)


@router.get("/backup/history", status_code=200)
async def get_history(
    company_id: UUID,
    limit: int = Query(10, ge=1, le=100),
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get backup history for company."""
    return await get_backup_history(db, company_id, limit)


@router.get("/rpo-rto", status_code=200)
async def get_rpo_rto(
    company_id: UUID,
    current_user=Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get RPO (Recovery Point Objective) and RTO (Recovery Time Objective)."""
    return await get_recovery_point_objective(company_id)
