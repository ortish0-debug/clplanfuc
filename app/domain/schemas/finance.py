"""
Pydantic v2 схемы для сериализации финансовых отчётов.
Decimal → str в JSON (без потери точности).
Все схемы — read-only (response-only), мутирующих валидаторов нет.
"""
from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator
from pydantic.functional_serializers import PlainSerializer

from app.use_cases.finance.calculator import (
    BusinessModel,
    TaxRegime,
)

# ---------------------------------------------------------------------------
# Кастомный тип: Decimal сериализуется как строка в JSON.
# Строка сохраняет точность и совместима с любым клиентом.
# ---------------------------------------------------------------------------

DecimalStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]


# ---------------------------------------------------------------------------
# Вспомогательные схемы
# ---------------------------------------------------------------------------


class MonthlyPeriodSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    month: int
    label: str
    first_day: date
    last_day: date


# ---------------------------------------------------------------------------
# ДДС (Cash Flow)
# ---------------------------------------------------------------------------


class CashFlowMonthSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: MonthlyPeriodSchema

    inflow: DecimalStr
    outflow: DecimalStr
    net_cash_flow: DecimalStr
    opening_balance: DecimalStr
    closing_balance: DecimalStr
    # Кассовый разрыв: 0.00 если разрыва нет, отрицательное значение — дефицит
    cash_gap: DecimalStr
    transfer_in: DecimalStr
    transfer_out: DecimalStr

    @field_validator("cash_gap", mode="before")
    @classmethod
    def validate_cash_gap(cls, v: Any) -> Decimal:
        # Гарантируем, что cash_gap ≤ 0
        d = Decimal(str(v))
        return min(d, Decimal("0.00"))


class CashFlowReportSchema(BaseModel):
    months: list[CashFlowMonthSchema]
    total_inflow: DecimalStr
    total_outflow: DecimalStr
    total_ncf: DecimalStr
    # True если хотя бы один месяц содержит кассовый разрыв
    has_cash_gap: bool

    @classmethod
    def from_months(cls, months: list[CashFlowMonthSchema]) -> "CashFlowReportSchema":
        total_inflow = sum((Decimal(m.inflow) for m in months), Decimal("0"))
        total_outflow = sum((Decimal(m.outflow) for m in months), Decimal("0"))
        return cls(
            months=months,
            total_inflow=total_inflow,
            total_outflow=total_outflow,
            total_ncf=total_inflow - total_outflow,
            has_cash_gap=any(Decimal(m.cash_gap) < Decimal("0") for m in months),
        )


# ---------------------------------------------------------------------------
# P&L (Прибыли и убытки)
# ---------------------------------------------------------------------------


class PnLMonthSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: MonthlyPeriodSchema

    revenue: DecimalStr
    cost_of_goods: DecimalStr
    gross_profit: DecimalStr
    operating_expenses: DecimalStr
    ebitda: DecimalStr
    depreciation: DecimalStr
    ebit: DecimalStr
    interest_expense: DecimalStr
    ebt: DecimalStr
    tax: DecimalStr
    net_profit: DecimalStr

    # ФОТ-расшифровка
    payroll_gross: DecimalStr
    payroll_insurance: DecimalStr
    payroll_total: DecimalStr

    # Вычисляемые производные — добавляются при сериализации
    gross_margin_pct: Optional[str] = None
    net_margin_pct: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        rev = Decimal(str(self.revenue))
        if rev > Decimal("0"):
            gp = Decimal(str(self.gross_profit))
            np_ = Decimal(str(self.net_profit))
            object.__setattr__(
                self,
                "gross_margin_pct",
                str((gp / rev * 100).quantize(Decimal("0.01"))),
            )
            object.__setattr__(
                self,
                "net_margin_pct",
                str((np_ / rev * 100).quantize(Decimal("0.01"))),
            )


class PnLReportSchema(BaseModel):
    months: list[PnLMonthSchema]
    total_revenue: DecimalStr
    total_net_profit: DecimalStr
    total_tax: DecimalStr
    avg_net_margin_pct: Optional[str] = None

    @classmethod
    def from_months(cls, months: list[PnLMonthSchema]) -> "PnLReportSchema":
        total_rev = sum((Decimal(m.revenue) for m in months), Decimal("0"))
        total_np = sum((Decimal(m.net_profit) for m in months), Decimal("0"))
        total_tax = sum((Decimal(m.tax) for m in months), Decimal("0"))
        avg_margin: Optional[str] = None
        if total_rev > Decimal("0"):
            avg_margin = str(
                (total_np / total_rev * 100).quantize(Decimal("0.01"))
            )
        return cls(
            months=months,
            total_revenue=total_rev,
            total_net_profit=total_np,
            total_tax=total_tax,
            avg_net_margin_pct=avg_margin,
        )


# ---------------------------------------------------------------------------
# 3-Way Balance Sheet
# ---------------------------------------------------------------------------


class BalanceSheetSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: MonthlyPeriodSchema

    # Активы
    cash_and_equivalents: DecimalStr
    accounts_receivable: DecimalStr
    total_assets: DecimalStr

    # Обязательства
    accounts_payable: DecimalStr
    short_term_debt: DecimalStr
    tax_payable: DecimalStr
    total_liabilities: DecimalStr

    # Капитал
    equity: DecimalStr

    # Проверка балансового уравнения: Assets = Liabilities + Equity (±1 коп. на округление)
    is_balanced: bool = True

    def model_post_init(self, __context: Any) -> None:
        assets = Decimal(str(self.total_assets))
        liabilities = Decimal(str(self.total_liabilities))
        equity = Decimal(str(self.equity))
        delta = abs(assets - (liabilities + equity))
        object.__setattr__(self, "is_balanced", delta <= Decimal("0.02"))


class BalanceReportSchema(BaseModel):
    months: list[BalanceSheetSchema]
    # Баланс на конец последнего периода
    final_assets: DecimalStr
    final_liabilities: DecimalStr
    final_equity: DecimalStr


# ---------------------------------------------------------------------------
# SaaS-метрики
# ---------------------------------------------------------------------------


class SaaSMetricsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: MonthlyPeriodSchema
    mrr: DecimalStr
    arr: DecimalStr
    new_mrr: DecimalStr
    churned_mrr: DecimalStr
    expansion_mrr: DecimalStr
    # Churn rate в % для удобства фронтенда
    churn_rate_pct: str
    customers_start: int
    customers_end: int

    @field_validator("churn_rate_pct", mode="before")
    @classmethod
    def format_churn_rate(cls, v: Any) -> str:
        # Принимаем Decimal 0.0–1.0, отдаём "12.50" (проценты)
        d = Decimal(str(v))
        return str((d * 100).quantize(Decimal("0.01")))


class SaaSReportSchema(BaseModel):
    months: list[SaaSMetricsSchema]
    peak_mrr: DecimalStr
    avg_churn_rate_pct: str


# ---------------------------------------------------------------------------
# Milestone-метрики
# ---------------------------------------------------------------------------


class MilestoneMetricsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: MonthlyPeriodSchema
    invoiced: DecimalStr
    collected: DecimalStr
    backlog: DecimalStr
    milestone_count_completed: int
    # Процент собираемости за месяц
    collection_rate_pct: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        inv = Decimal(str(self.invoiced))
        col = Decimal(str(self.collected))
        if inv > Decimal("0"):
            rate = (col / inv * 100).quantize(Decimal("0.01"))
            object.__setattr__(self, "collection_rate_pct", str(rate))


class MilestoneReportSchema(BaseModel):
    months: list[MilestoneMetricsSchema]
    total_invoiced: DecimalStr
    total_collected: DecimalStr
    total_backlog: DecimalStr
    total_milestones_completed: int


# ---------------------------------------------------------------------------
# Корневая схема FinancialReportSchema
# ---------------------------------------------------------------------------


class ReportTotalsSchema(BaseModel):
    total_revenue: DecimalStr
    total_inflow: DecimalStr
    total_outflow: DecimalStr
    net_cash_flow: DecimalStr
    total_net_profit: DecimalStr
    total_tax: DecimalStr
    final_balance: DecimalStr
    periods_count: int


class FinancialReportSchema(BaseModel):
    """
    Корневой объект ответа эндпоинта GET /reports/financial.
    Содержит все три отчёта и опциональные бизнес-модельные метрики.
    """

    company_id: UUID
    start_date: date
    end_date: date
    business_model: BusinessModel
    tax_regime: TaxRegime
    currency: str
    generated_at: str  # ISO 8601

    cash_flow: CashFlowReportSchema
    pnl: PnLReportSchema
    balance: BalanceReportSchema

    # Присутствуют только при соответствующей бизнес-модели
    saas_metrics: Optional[SaaSReportSchema] = None
    milestone_metrics: Optional[MilestoneReportSchema] = None

    totals: ReportTotalsSchema
