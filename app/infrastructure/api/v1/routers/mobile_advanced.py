"""Advanced mobile API (push notifications, Prophet forecast)."""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.api.v1.dependencies.auth import CanViewDashboard
from app.infrastructure.database.session import get_db
from app.services.forecast_advanced_service import generate_prophet_forecast
from app.services.mobile_push_service import register_device_token, send_mobile_push_notification

router = APIRouter(prefix="/companies/{company_id}/mobile", tags=["Mobile Advanced"])


class RegisterDeviceRequest(BaseModel):
    device_token: str
    os_type: str  # 'ios' or 'android'


class SendPushRequest(BaseModel):
    user_id: UUID
    title: str
    body: str
    device_tokens: list[str]


@router.post("/register-device", status_code=201)
async def register_device(
    company_id: UUID,
    request: RegisterDeviceRequest,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Register mobile device token (FCM/APNS)."""
    result = await register_device_token(
        user_id=current_user.id,
        device_token=request.device_token,
        os_type=request.os_type,
    )
    return result


@router.post("/send-push", status_code=200)
async def send_push(
    company_id: UUID,
    request: SendPushRequest,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send push notification to mobile devices."""
    success = await send_mobile_push_notification(
        company_id=company_id,
        user_id=request.user_id,
        title=request.title,
        body=request.body,
        device_tokens=request.device_tokens,
    )

    return {
        "status": "success" if success else "failed",
        "user_id": str(request.user_id),
        "device_count": len(request.device_tokens),
    }


@router.get("/analytics/forecast/prophet", status_code=200)
async def get_prophet_forecast(
    company_id: UUID,
    periods: int = 90,
    current_user=Depends(CanViewDashboard),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get advanced Prophet forecast with seasonality and holidays."""
    return await generate_prophet_forecast(db, company_id, periods)
