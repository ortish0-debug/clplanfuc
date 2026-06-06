"""Mobile push notification service (FCM/APNS)."""
from uuid import UUID
import httpx

# Simulated FCM/APNS client
class PushNotificationClient:
    """Simulated Firebase Cloud Messaging + Apple Push Notification client."""

    async def send_fcm(self, device_token: str, title: str, body: str) -> bool:
        """Send to Android via FCM."""
        # Production: firebase_admin.messaging.send(Message(...))
        print(f"📱 FCM → {device_token[:20]}...: {title}")
        return True

    async def send_apns(self, device_token: str, title: str, body: str) -> bool:
        """Send to iOS via APNS."""
        # Production: APNs HTTP/2 connection
        print(f"🍎 APNS → {device_token[:20]}...: {title}")
        return True


# Global client
push_client = PushNotificationClient()


async def send_mobile_push_notification(
    company_id: UUID, user_id: UUID, title: str, body: str, device_tokens: list[str] = None
) -> bool:
    """
    Send push notifications to mobile devices (Firebase + APNS).
    """
    if not device_tokens:
        device_tokens = []  # In production: fetch from DB

    if not device_tokens:
        return False

    try:
        results = []

        for token in device_tokens:
            if token.startswith("ios_"):
                # Apple APNS
                success = await push_client.send_apns(token, title, body)
            else:
                # Android FCM
                success = await push_client.send_fcm(token, title, body)

            results.append(success)

        success_count = sum(results)
        print(f"📱 [Mobile Push] Направлен асинхронный пуш через FCM/APNS на устройство пользователя {user_id}")

        return all(results) if results else False

    except Exception as e:
        print(f"❌ Mobile push error: {str(e)}")
        return False


async def register_device_token(
    user_id: UUID, device_token: str, os_type: str
) -> dict:
    """
    Register device token for user.
    In production: save to DB with last_used timestamp.
    """
    # Validate token format
    if not device_token or len(device_token) < 20:
        return {"status": "error", "message": "Invalid device token"}

    # Validate OS type
    if os_type not in ["ios", "android"]:
        return {"status": "error", "message": "Invalid OS type"}

    # In production: DB save with upsert
    # await db.execute(
    #     insert(DeviceToken)
    #     .values(user_id=user_id, token=device_token, os_type=os_type)
    #     .on_conflict_do_update(...)
    # )

    return {
        "status": "success",
        "user_id": str(user_id),
        "device_token": device_token[:30] + "...",
        "os_type": os_type,
        "registered_at": __import__('datetime').datetime.utcnow().isoformat(),
    }


async def send_anomaly_alert_push(
    company_id: UUID, user_id: UUID, anomaly_data: dict, device_tokens: list[str]
) -> bool:
    """
    Send anomaly alert as push notification.
    """
    z_score = anomaly_data.get("z_score", 0)
    amount = anomaly_data.get("amount", 0)

    title = f"🚨 Критическая аномалия в расходах"
    body = f"Z-score: {z_score:.2f}σ | Сумма: {amount:.0f}₽"

    return await send_mobile_push_notification(company_id, user_id, title, body, device_tokens)
