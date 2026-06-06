"""Pydantic-схемы для налоговых отчётов (Sprint 20)."""
from pydantic import BaseModel


class VatDeclarationResponse(BaseModel):
    year: int
    quarter: int
    total_output_vat: str
    total_input_vat: str
    net_vat_to_pay: str


class ProfitTaxReportResponse(BaseModel):
    year: int
    total_revenues: str
    total_expenses: str
    tax_base: str
    tax_rate: str
    tax_amount: str
