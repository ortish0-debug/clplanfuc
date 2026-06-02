"""Russian tax reporting API."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db
from app.services.russian_tax_forms_service import calculate_tax_declaration

router = APIRouter(prefix="/companies/{company_id}/reports", tags=["Russian Taxes"])


@router.get("/usn", status_code=200)
async def get_usn_declaration(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get УСН (Simplified Tax System) declaration."""
    return await calculate_tax_declaration(db, company_id, "usn")


@router.get("/vat", status_code=200)
async def get_vat_declaration(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get НДС (VAT) declaration."""
    return await calculate_tax_declaration(db, company_id, "vat")


@router.get("/3ndfl", status_code=200)
async def get_3ndfl_declaration(
    company_id: UUID,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get 3-НДФЛ (Personal Income Tax) declaration."""
    return await calculate_tax_declaration(db, company_id, "3ndfl")
