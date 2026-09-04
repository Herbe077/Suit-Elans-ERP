"""PDFs con ReportLab: ficha técnica, ticket, cotización y comprobante interno."""
from io import BytesIO

from reportlab.lib.pagesizes import A4, mm
from reportlab.lib.units import mm as mmu
from reportlab.pdfgen import canvas

BORGO = (0x37 / 255, 0x00, 0x03 / 255)


def _header(c, h, titulo: str, subtitulo: str = ""):
    c.setFillColorRGB(*BORGO)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mmu, h - 20 * mmu, f"Suit Elans — {titulo}")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 11)
    if subtitulo:
        c.drawString(20 * mmu, h - 30 * mmu, subtitulo)
    return h - 42 * mmu


def ficha_medidas_pdf(cliente: str, medidas: dict, observaciones: str = "") -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mmu, h - 20 * mmu, "Suit Elans — Ficha Técnica de Medidas")
    c.setFont("Helvetica", 11)
    c.drawString(20 * mmu, h - 30 * mmu, f"Cliente: {cliente}")
    y = h - 42 * mmu
    c.setFont("Helvetica", 10)
    for k, v in medidas.items():
        if v is None:
            continue
        c.drawString(20 * mmu, y, f"{k}: {v} cm")
        y -= 6 * mmu
        if y < 30 * mmu:
            c.showPage()
            y = h - 20 * mmu
    if observaciones:
        c.setFont("Helvetica-Oblique", 10)
        c.drawString(20 * mmu, y - 8 * mmu, f"Obs: {observaciones[:300]}")
    c.showPage()
    c.save()
    return buf.getvalue()


def ticket_pedido_pdf(folio: str, cliente: str, prendas: list, total: float, anticipo: float) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(80 * mm, 200 * mm))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(5 * mmu, 190 * mmu, "SUIT ELANS")
    c.setFont("Helvetica", 9)
    c.drawString(5 * mmu, 184 * mmu, f"Folio: {folio}")
    c.drawString(5 * mmu, 179 * mmu, f"Cliente: {cliente[:28]}")
    y = 170 * mmu
    for p in prendas:
        c.drawString(5 * mmu, y, f"{p['tipo']}  S/{p['precio']:.2f}")
        y -= 5 * mmu
    c.drawString(5 * mmu, y - 5 * mmu, f"Total: S/{total:.2f}")
    c.drawString(5 * mmu, y - 10 * mmu, f"Anticipo: S/{anticipo:.2f}")
    c.drawString(5 * mmu, y - 15 * mmu, f"Saldo: S/{total - anticipo:.2f}")
    c.showPage()
    c.save()
    return buf.getvalue()


def cotizacion_pdf(folio: str, cliente: str, lineas: list, subtotal: float,
                   descuento_pct: float, total: float, validez: str = "") -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = _header(c, h, "Cotización", f"{folio}  ·  Cliente: {cliente[:60]}")
    c.setFont("Helvetica", 10)
    for l in lineas:
        c.drawString(20 * mmu, y, f"{l['cantidad']} x {l['concepto'][:60]}  S/{l['importe']:.2f}")
        y -= 6 * mmu
    y -= 4 * mmu
    c.drawString(20 * mmu, y, f"Subtotal: S/{subtotal:.2f}   Descuento: {descuento_pct}%")
    c.drawString(20 * mmu, y - 6 * mmu, f"TOTAL: S/{total:.2f}")
    if validez:
        c.setFont("Helvetica-Oblique", 10)
        c.drawString(20 * mmu, y - 12 * mmu, f"Válida hasta: {validez}")
    c.showPage()
    c.save()
    return buf.getvalue()


def comprobante_pdf(folio: str, cliente: str, lineas: list, subtotal: float,
                    igv_pct: float, igv: float, total: float) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = _header(c, h, "Comprobante de pago", f"{folio}  ·  Cliente: {cliente[:60]}")
    c.setFont("Helvetica", 10)
    for l in lineas:
        c.drawString(20 * mmu, y, f"{l['concepto'][:70]}  S/{l['importe']:.2f}")
        y -= 6 * mmu
    y -= 4 * mmu
    c.drawString(20 * mmu, y, f"Subtotal: S/{subtotal:.2f}")
    c.drawString(20 * mmu, y - 6 * mmu, f"IGV ({igv_pct}%): S/{igv:.2f}")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mmu, y - 13 * mmu, f"TOTAL: S/{total:.2f}")
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(20 * mmu, y - 19 * mmu, "Documento interno — no válido como comprobante fiscal electrónico.")
    c.showPage()
    c.save()
    return buf.getvalue()
