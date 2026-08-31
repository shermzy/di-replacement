"""Generate a small text-and-grid invoice for local E2E testing."""
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph

out = "sample_invoice.pdf"
styles = getSampleStyleSheet()
story = [
    Paragraph("ACME SUPPLIES PTE LTD", styles["Heading1"]),
    Paragraph("123 Industry Road, Singapore 123456", styles["Normal"]),
    Spacer(1, 8 * mm),
    Paragraph(
        "Invoice No: INV-2024-001&nbsp;&nbsp;&nbsp;"
        "Invoice Date: 12/03/2024&nbsp;&nbsp;&nbsp;"
        "Due Date: 26/03/2024&nbsp;&nbsp;&nbsp;"
        "PO Number: PO-8877",
        styles["Normal"],
    ),
    Paragraph("Bill To: HiddenXP Pte Ltd&nbsp;&nbsp;&nbsp;Currency: SGD", styles["Normal"]),
    Spacer(1, 6 * mm),
]

table = Table(
    [
        ["Item", "Description", "Qty", "Unit Price", "Amount"],
        ["1", "Widget A", "10", "50.00", "500.00"],
        ["2", "Widget B", "5", "100.00", "500.00"],
        ["", "Total", "", "", "1,000.00"],
    ],
    colWidths=[14 * mm, 70 * mm, 14 * mm, 28 * mm, 28 * mm],
)
table.setStyle(TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
]))
story.extend([
    table,
    Spacer(1, 6 * mm),
    Paragraph(
        "Subtotal: 1,000.00&nbsp;&nbsp;&nbsp;GST (9%): 90.00&nbsp;&nbsp;&nbsp;"
        "Total: SGD 1,090.00&nbsp;&nbsp;&nbsp;Amount Due: 1,090.00",
        styles["Normal"],
    ),
])
SimpleDocTemplate(out, pagesize=A4).build(story)
print(f"wrote {out}")
