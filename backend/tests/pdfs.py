import io

from reportlab.pdfgen import canvas


def make_pdf(pages: list[str]) -> bytes:
    """A PDF with one page per entry; an empty string gives a page without text."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for text in pages:
        y = 800
        for line in text.split("\n"):
            pdf.drawString(72, y, line)
            y -= 14
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
