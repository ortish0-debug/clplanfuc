"""Bank OAuth2 authentication routers (Sprint 22)."""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.bank_integration import BankIntegration
from app.infrastructure.api.v1.dependencies.auth import CanWriteFinance, CurrentUser
from app.infrastructure.database.session import get_db
from app.infrastructure.security.crypto import decrypt_data, encrypt_data
from app.services.audit_service import log_audit_action
from app.services.tinkoff_service import sync_tinkoff_statements
from app.services.sberbank_service import sync_sberbank_statements

router = APIRouter(
    prefix="/companies/{company_id}/bank-integrations",
    tags=["Bank Integrations"],
)


class TinkoffSyncRequest(BaseModel):
    """Request schema for Tinkoff statement sync."""

    account_id: UUID
    from_date: date
    to_date: date


class SberbankSyncRequest(BaseModel):
    """Request schema for Sberbank statement sync."""

    account_id: UUID
    from_date: date
    to_date: date


async def get_decrypted_bank_tokens(
    db: AsyncSession,
    company_id: UUID,
    bank_name: str,
) -> dict:
    """
    Retrieve and decrypt bank tokens for a company.

    Returns:
        dict with 'access_token' and 'refresh_token' keys, or raises HTTPException if not found.
    """
    result = await db.execute(
        select(BankIntegration).where(
            and_(
                BankIntegration.company_id == company_id,
                BankIntegration.bank_name == bank_name,
                BankIntegration.status == "active",
            )
        )
    )
    integration = result.scalar_one_or_none()

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active {bank_name} integration found for company {company_id}",
        )

    # Decrypt tokens
    access_token = decrypt_data(integration.encrypted_access_token)
    refresh_token = (
        decrypt_data(integration.encrypted_refresh_token)
        if integration.encrypted_refresh_token
        else None
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": integration.expires_at.isoformat() if integration.expires_at else None,
    }


@router.post(
    "/tinkoff/connect",
    status_code=status.HTTP_201_CREATED,
    summary="Connect to Tinkoff API via OAuth2",
)
async def connect_tinkoff(
    company_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept OAuth2 callback code from Tinkoff and store encrypted tokens.

    Stub implementation: in production, exchange code for tokens via Tinkoff API.
    For now: mock token generation for testing.
    """
    body = await request.json()
    oauth_code = body.get("code")

    if not oauth_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'code' parameter",
        )

    # Stub: mock token exchange (in production, call Tinkoff OAuth2 endpoint)
    access_token = f"tinkoff_access_{oauth_code[:16]}"
    refresh_token = f"tinkoff_refresh_{oauth_code[:16]}"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)

    # Encrypt tokens before storage
    encrypted_access = encrypt_data(access_token)
    encrypted_refresh = encrypt_data(refresh_token)

    # Create bank integration record
    integration = BankIntegration(
        company_id=company_id,
        bank_name="tinkoff",
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        expires_at=expires_at,
        status="active",
    )
    db.add(integration)
    await db.flush()

    # Audit log
    client_ip = request.client.host if request.client else "unknown"
    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="bank.integration_created",
        target_type="BankIntegration",
        target_id=str(integration.id),
        ip_address=client_ip,
    )

    await db.commit()

    return {
        "id": str(integration.id),
        "bank_name": "tinkoff",
        "status": "active",
        "created_at": integration.created_at.isoformat(),
    }


@router.post(
    "/tinkoff/sync",
    status_code=status.HTTP_200_OK,
    summary="Manually sync Tinkoff bank statements",
)
async def sync_tinkoff(
    company_id: UUID,
    body: TinkoffSyncRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger manual synchronization of Tinkoff statements for a given account.

    Args:
        company_id: Company ID (path param)
        body: Request body with account_id, from_date, to_date
        current_user: Authenticated user (IDOR protection)
        db: Database session

    Returns:
        JSON with import status and count of synced transactions
    """
    imported_count = await sync_tinkoff_statements(
        db=db,
        company_id=company_id,
        account_id=body.account_id,
        from_date=body.from_date,
        to_date=body.to_date,
    )

    await db.commit()

    return {
        "status": "success",
        "imported_count": imported_count,
    }


@router.post(
    "/sberbank/connect",
    status_code=status.HTTP_201_CREATED,
    summary="Connect to Sberbank Business API via OAuth2",
)
async def connect_sberbank(
    company_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept OAuth2 callback code from Sberbank Business and store encrypted tokens.

    Stub implementation: in production, exchange code for tokens via Sberbank API.
    For now: mock token generation for testing.
    """
    body = await request.json()
    oauth_code = body.get("code")

    if not oauth_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'code' parameter",
        )

    # Stub: mock token exchange (in production, call Sberbank OAuth2 endpoint)
    access_token = f"sberbank_access_{oauth_code[:16]}"
    refresh_token = f"sberbank_refresh_{oauth_code[:16]}"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)

    # Encrypt tokens before storage
    encrypted_access = encrypt_data(access_token)
    encrypted_refresh = encrypt_data(refresh_token)

    # Create bank integration record
    integration = BankIntegration(
        company_id=company_id,
        bank_name="sberbank",
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        expires_at=expires_at,
        status="active",
    )
    db.add(integration)
    await db.flush()

    # Audit log
    client_ip = request.client.host if request.client else "unknown"
    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="bank.sberbank_connected",
        target_type="BankIntegration",
        target_id=str(integration.id),
        ip_address=client_ip,
    )

    await db.commit()

    return {
        "id": str(integration.id),
        "bank_name": "sberbank",
        "status": "active",
        "created_at": integration.created_at.isoformat(),
    }


@router.post("/sberbank/sync", status_code=status.HTTP_200_OK)
async def sync_sberbank(
    company_id: UUID,
    body: SberbankSyncRequest,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """Trigger manual synchronization of Sberbank statements."""
    imported_count = await sync_sberbank_statements(
        db=db,
        company_id=company_id,
        account_id=body.account_id,
        from_date=body.from_date,
        to_date=body.to_date,
    )
    await db.commit()
    return {"status": "success", "imported_count": imported_count}


@router.post("/alfa/connect", status_code=status.HTTP_201_CREATED)
async def connect_alfa(
    company_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """Connect to Alfa-Bank API via OAuth2."""
    body = await request.json()
    oauth_code = body.get("code")
    if not oauth_code:
        raise HTTPException(status_code=400, detail="Missing 'code' parameter")

    access_token = f"alfa_access_{oauth_code[:16]}"
    refresh_token = f"alfa_refresh_{oauth_code[:16]}"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)

    encrypted_access = encrypt_data(access_token)
    encrypted_refresh = encrypt_data(refresh_token)

    integration = BankIntegration(
        company_id=company_id,
        bank_name="alfa",
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        expires_at=expires_at,
        status="active",
    )
    db.add(integration)
    await db.flush()

    client_ip = request.client.host if request.client else "unknown"
    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="bank.alfa_connected",
        target_type="BankIntegration",
        target_id=str(integration.id),
        ip_address=client_ip,
    )
    await db.commit()

    return {
        "id": str(integration.id),
        "bank_name": "alfa",
        "status": "active",
        "created_at": integration.created_at.isoformat(),
    }


@router.post("/tochka/connect", status_code=status.HTTP_201_CREATED)
async def connect_tochka(
    company_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(CanWriteFinance),
    db: AsyncSession = Depends(get_db),
):
    """Connect to Tochka Bank API via OAuth2."""
    body = await request.json()
    oauth_code = body.get("code")
    if not oauth_code:
        raise HTTPException(status_code=400, detail="Missing 'code' parameter")

    access_token = f"tochka_access_{oauth_code[:16]}"
    refresh_token = f"tochka_refresh_{oauth_code[:16]}"
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)

    encrypted_access = encrypt_data(access_token)
    encrypted_refresh = encrypt_data(refresh_token)

    integration = BankIntegration(
        company_id=company_id,
        bank_name="tochka",
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        expires_at=expires_at,
        status="active",
    )
    db.add(integration)
    await db.flush()

    client_ip = request.client.host if request.client else "unknown"
    await log_audit_action(
        db=db,
        company_id=company_id,
        user_id=current_user.user_id,
        action="bank.tochka_connected",
        target_type="BankIntegration",
        target_id=str(integration.id),
        ip_address=client_ip,
    )
    await db.commit()

    return {
        "id": str(integration.id),
        "bank_name": "tochka",
        "status": "active",
        "created_at": integration.created_at.isoformat(),
    }
