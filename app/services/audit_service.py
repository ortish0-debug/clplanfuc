"""Audit logging service for security and compliance tracking."""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.audit import AuditLog

logger = logging.getLogger(__name__)


async def log_audit_action(
    db: AsyncSession,
    company_id: Optional[UUID],
    user_id: Optional[UUID],
    action: str,
    target_type: str,
    target_id: Optional[UUID],
    ip_address: str,
) -> AuditLog:
    """
    Log an audit action to the audit trail.

    Args:
        db: Database session
        company_id: Company ID (optional)
        user_id: User ID (optional)
        action: Action code (e.g., 'user.login', 'ledger.entry')
        target_type: Type of target object (e.g., 'user', 'transaction')
        target_id: ID of the target object (optional)
        ip_address: Client IP address

    Returns:
        Created AuditLog record
    """
    try:
        audit_log = AuditLog(
            company_id=company_id,
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
        )
        db.add(audit_log)
        await db.flush()
        return audit_log
    except Exception as e:
        logger.exception(
            "Failed to log audit action: %s (user_id=%s, company_id=%s, action=%s)",
            type(e).__name__,
            user_id,
            company_id,
            action,
        )
        raise
