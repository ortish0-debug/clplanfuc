"""Payroll tax calculator for Russian FZ-152 compliance."""


def calculate_ru_payroll_taxes(salary: float, is_smb: bool = True) -> dict:
    """
    Calculate Russian payroll taxes: NDFL + contributions.

    Args:
        salary: Gross salary in rubles
        is_smb: True = SMB rates, False = standard rates

    Returns:
        {"ndfl": float, "contributions": float, "net_salary": float}
    """
    # NDFL (Personal income tax): 13% up to 5M RUB
    ndfl = salary * 0.13

    # Social contributions (insurance)
    mrot = 20000  # Conditional MROT
    if is_smb:
        # SMB reduced rate: 30% up to MROT, 15% above
        if salary <= mrot:
            contributions = salary * 0.30
        else:
            contributions = (mrot * 0.30) + ((salary - mrot) * 0.15)
    else:
        # Standard: 30% across entire salary
        contributions = salary * 0.30

    net_salary = salary - ndfl - contributions

    return {
        "ndfl": round(ndfl, 2),
        "contributions": round(contributions, 2),
        "net_salary": round(net_salary, 2),
    }


def generate_2ndfl_data(user_name: str, salary: float, tax_dict: dict) -> dict:
    """Generate 2-NDFL report data."""
    return {
        "full_name": user_name,
        "tax_year": 2026,
        "total_income": round(salary, 2),
        "tax_accrued": round(tax_dict["ndfl"], 2),
        "tax_withheld": round(tax_dict["ndfl"], 2),
        "net_amount": round(tax_dict["net_salary"], 2),
    }


def calculate_employee_commission(sales_volume: float, rate: float = 0.05) -> float:
    """Calculate sales commission (default 5%)."""
    return round(sales_volume * rate, 2)
