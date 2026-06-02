"""
FastAPI роутер: Проекты компании (Спринт 6).

Эндпоинты:
  GET    /companies/{id}/projects              — список проектов (все роли)
  POST   /companies/{id}/projects              — создать (OWNER/ADMIN/ACCOUNTANT)
  GET    /companies/{id}/projects/{project_id} — один проект (все роли)
  PATCH  /companies/{id}/projects/{project_id} — обновить (OWNER/ADMIN/ACCOUNTANT)
  DELETE /companies/{id}/projects/{project_id} — мягкое удаление (OWNER/ADMIN/ACCOUNTANT)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.projects import Project
from app.domain.schemas.projects import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from app.infrastructure.api.v1.dependencies.auth import (
    CanViewDashboard,
    CanWriteFinance,
    CurrentUser,
)
from app.infrastructure.database.session import get_db

router = APIRouter(tags=["Проекты"])


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def _get_project_or_404(
    db: AsyncSession,
    project_id: UUID,
    company_id: UUID,
) -> Project:
    result = await db.execute(
        select(Project).where(
            and_(
                Project.id == project_id,
                Project.company_id == company_id,
                Project.is_deleted.is_(False),
            )
        )
    )
    proj = result.scalar_one_or_none()
    if not proj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Проект {project_id} не найден.",
        )
    return proj


# ─────────────────────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/projects",
    response_model=list[ProjectResponse],
    summary="Список проектов компании",
)
async def list_projects(
    company_id:   UUID,
    is_active:    Optional[bool] = Query(None, description="Фильтр по статусу активности"),
    current_user: CurrentUser    = Depends(CanViewDashboard),
    db:           AsyncSession   = Depends(get_db),
) -> list[ProjectResponse]:
    filters = [
        Project.company_id == company_id,
        Project.is_deleted.is_(False),
    ]
    if is_active is not None:
        filters.append(Project.is_active.is_(is_active))

    result = await db.execute(
        select(Project).where(and_(*filters)).order_by(Project.name)
    )
    return [ProjectResponse.model_validate(p) for p in result.scalars().all()]


# ─────────────────────────────────────────────────────────────────────────────
# GET ONE
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/companies/{company_id}/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Получить проект по ID",
)
async def get_project(
    company_id:   UUID,
    project_id:   UUID,
    current_user: CurrentUser  = Depends(CanViewDashboard),
    db:           AsyncSession = Depends(get_db),
) -> ProjectResponse:
    proj = await _get_project_or_404(db, project_id, company_id)
    return ProjectResponse.model_validate(proj)


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/companies/{company_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать проект",
)
async def create_project(
    company_id:   UUID,
    body:         ProjectCreate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> ProjectResponse:
    proj = Project(
        id=uuid.uuid4(),
        company_id=company_id,
        name=body.name,
        description=body.description,
        color=body.color,
        is_active=True,
        is_deleted=False,
    )
    db.add(proj)
    await db.flush()
    return ProjectResponse.model_validate(proj)


# ─────────────────────────────────────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────────────────────────────────────


@router.patch(
    "/companies/{company_id}/projects/{project_id}",
    response_model=ProjectResponse,
    summary="Обновить проект",
)
async def update_project(
    company_id:   UUID,
    project_id:   UUID,
    body:         ProjectUpdate,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> ProjectResponse:
    proj = await _get_project_or_404(db, project_id, company_id)

    update_data = body.model_dump(exclude_none=True)
    for field_name, value in update_data.items():
        setattr(proj, field_name, value)

    await db.flush()
    return ProjectResponse.model_validate(proj)


# ─────────────────────────────────────────────────────────────────────────────
# SOFT DELETE
# ─────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/companies/{company_id}/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить проект (мягкое удаление)",
    description=(
        "Помечает проект как удалённый (is_deleted=True). "
        "Связанные бюджеты и транзакции сохраняются."
    ),
)
async def delete_project(
    company_id:   UUID,
    project_id:   UUID,
    current_user: CurrentUser  = Depends(CanWriteFinance),
    db:           AsyncSession = Depends(get_db),
) -> None:
    proj = await _get_project_or_404(db, project_id, company_id)
    proj.is_deleted = True
    proj.deleted_at = datetime.now(tz=timezone.utc)
    proj.is_active  = False
    await db.flush()
