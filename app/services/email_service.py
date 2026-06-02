"""Email integration and OCR service."""
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.counterparties import Counterparty
from app.domain.models.documents import Document


def parse_invoice_text(text: str) -> dict:
    """
    Parse invoice text using regex.
    Extracts: INN, document number, date, total amount.
    """
    result = {
        'inn': None,
        'doc_number': None,
        'doc_date': None,
        'total_amount': None,
    }

    # INN pattern (10 or 12 digits)
    inn_match = re.search(r'\b(\d{10}|\d{12})\b', text)
    if inn_match:
        result['inn'] = inn_match.group(1)

    # Document number (e.g., "№ 123-456" or "№123456")
    doc_match = re.search(r'№\s*(\d+[-/]?\d*)', text, re.IGNORECASE)
    if doc_match:
        result['doc_number'] = doc_match.group(1)

    # Date (YYYY-MM-DD or DD.MM.YYYY)
    date_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})', text)
    if date_match:
        result['doc_date'] = date_match.group(1)

    # Amount (e.g., "100000.00" or "100 000,00")
    amount_match = re.search(r'(сумма|итого|всего)[\s:]*(\d{1,3}[\s,.\d]*\d{2})', text, re.IGNORECASE)
    if amount_match:
        amount_str = amount_match.group(2).replace(' ', '').replace(',', '.')
        try:
            result['total_amount'] = float(amount_str)
        except ValueError:
            pass

    return result


async def poll_email_and_ocr_invoices(db: AsyncSession, company_id: UUID) -> dict:
    """
    Poll email and create Document records from OCR'd invoices.

    Simulates IMAP connection and multipart parsing.
    """
    created_docs = 0
    errors = 0

    # Simulate IMAP connection (in production: use imaplib)
    # mock_emails = [
    #     {
    #         'from': 'supplier@example.com',
    #         'subject': 'Invoice INV-001',
    #         'attachments': [
    #             {'filename': 'invoice.pdf', 'content': '...'}
    #         ]
    #     }
    # ]

    # For now, simulate parsing a mock invoice text
    mock_invoice_text = """
    Счёт-фактура
    Продавец: ООО Поставщик
    ИНН: 7701234567
    № счета: 123-456
    Дата: 2026-06-01
    Итого: 150000.50
    """

    try:
        parsed = parse_invoice_text(mock_invoice_text)

        if parsed['inn']:
            counterparty_result = await db.execute(
                select(Counterparty).where(
                    Counterparty.company_id == company_id,
                    Counterparty.inn == parsed['inn'],
                )
            )
            counterparty = counterparty_result.scalar_one_or_none()

            doc = Document(
                company_id=company_id,
                doc_type='invoice',
                doc_number=parsed['doc_number'] or 'OCR-AUTO',
                total_amount=parsed['total_amount'] or 0.0,
                currency='RUB',
                counterparty_id=counterparty.id if counterparty else None,
                status='draft',
            )
            db.add(doc)
            created_docs += 1
            print(f"📥 Email OCR: Created document {doc.doc_number} from email")

    except Exception as e:
        errors += 1
        print(f"❌ Email OCR error: {str(e)}")

    return {
        "status": "success",
        "created_documents": created_docs,
        "errors": errors,
    }
