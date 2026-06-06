"""CSV import service."""
from datetime import datetime


def parse_csv_transactions(file_content: str) -> list[dict]:
    """Parse CSV transactions: date,amount,type,description."""
    lines = file_content.strip().split('\n')
    transactions = []

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 3:
            continue

        try:
            transactions.append({
                'plan_date': datetime.strptime(parts[0], '%Y-%m-%d').date(),
                'amount': float(parts[1]),
                'transaction_type': parts[2],
                'description': parts[3] if len(parts) > 3 else '',
            })
        except (ValueError, IndexError):
            continue

    return transactions
