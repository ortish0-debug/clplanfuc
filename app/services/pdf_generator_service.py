"""PDF generation service with ReportLab and Cyrillic support."""
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

# Register Cyrillic font (using system fonts)
try:
    pdfmetrics.registerFont(TTFont('Arial', 'C:\\Windows\\Fonts\\arial.ttf'))
    pdfmetrics.registerFont(TTFont('ArialBold', 'C:\\Windows\\Fonts\\arialbd.ttf'))
except Exception:
    # Fallback if fonts not found
    pass


def num_to_words(amount: float) -> str:
    """Convert number to Russian words (simplified)."""
    units = ['', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']
    tens = ['', 'десять', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто']

    rubles = int(amount)
    kopeks = int((amount - rubles) * 100)

    if rubles == 0:
        return f"{kopeks} копеек"
    return f"{rubles} рублей {kopeks} копеек"


def generate_invoice_pdf(
    company_name: str,
    invoice_number: str,
    invoice_date: str,
    lines: list[dict],
    total_amount: float,
    counterparty: str = "Контрагент",
) -> bytes:
    """
    Generate PDF invoice with ReportLab.

    Args:
        company_name: Company name
        invoice_number: Invoice number
        invoice_date: Invoice date (YYYY-MM-DD)
        lines: List of dicts with keys: name, quantity, price, amount
        total_amount: Total sum
        counterparty: Counterparty name

    Returns:
        PDF as bytes
    """
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=A4, rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName='Arial',
        fontSize=14,
        textColor=colors.black,
        spaceAfter=6,
        alignment=TA_CENTER,
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Normal'],
        fontName='Arial',
        fontSize=10,
        textColor=colors.black,
    )

    normal_style = ParagraphStyle(
        'Normal',
        fontName='Arial',
        fontSize=9,
        textColor=colors.black,
    )

    story = []

    # Header
    story.append(Paragraph(f"<b>{company_name}</b>", title_style))
    story.append(Spacer(1, 0.3*cm))

    # Invoice info
    story.append(Paragraph(f"Счёт № {invoice_number} от {invoice_date}", heading_style))
    story.append(Paragraph(f"Получатель: {counterparty}", normal_style))
    story.append(Spacer(1, 0.5*cm))

    # Table data
    table_data = [
        ['Наименование', 'Кол-во', 'Цена', 'Сумма']
    ]
    for line in lines:
        table_data.append([
            Paragraph(line.get('name', ''), normal_style),
            str(line.get('quantity', 0)),
            f"{line.get('price', 0):.2f}₽",
            f"{line.get('amount', 0):.2f}₽",
        ])

    # Add total row
    table_data.append([
        Paragraph("<b>ИТОГО</b>", heading_style),
        '',
        '',
        Paragraph(f"<b>{total_amount:.2f}₽</b>", heading_style),
    ])

    table = Table(table_data, colWidths=[8*cm, 2*cm, 2.5*cm, 2.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Arial'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
    ]))

    story.append(table)
    story.append(Spacer(1, 0.8*cm))

    # Amount in words
    amount_words = num_to_words(total_amount)
    story.append(Paragraph(f"<b>Сумма прописью:</b> {amount_words}", normal_style))
    story.append(Spacer(1, 1*cm))

    # Signature block
    story.append(Paragraph("_______________________      _______________________", normal_style))
    story.append(Paragraph("Бухгалтер                      Руководитель", normal_style))

    # Build PDF
    doc.build(story)
    bio.seek(0)
    return bio.getvalue()
