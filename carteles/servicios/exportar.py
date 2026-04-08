"""
exportar.py
Servicios de exportación de carteles a Excel y PDF.
Recibe un queryset ya filtrado desde la vista de informes.
"""

import io
import os
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)


# ── Excel ─────────────────────────────────────────────────────────────────────

def exportar_excel(queryset) -> bytes:
    """
    Genera un archivo Excel con todos los datos del queryset.
    Devuelve los bytes del archivo .xlsx.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Carteles"

    # ── Estilos ───────────────────────────────────────────────────────────────
    color_header = "1E3A5F"
    font_header  = Font(bold=True, color="FFFFFF", size=10)
    fill_header  = PatternFill("solid", fgColor=color_header)
    alin_centro  = Alignment(horizontal="center", vertical="center", wrap_text=True)
    alin_izq     = Alignment(horizontal="left",   vertical="center", wrap_text=True)

    fill_par  = PatternFill("solid", fgColor="EEF2F7")
    fill_impar = PatternFill("solid", fgColor="FFFFFF")

    # ── Encabezados ───────────────────────────────────────────────────────────
    columnas = [
        ("ID",               8),
        ("Kobo ID",          14),
        ("Fecha captura",    16),
        ("Operador",         16),
        ("Tipo cartel",      14),
        ("Estado físico",    14),
        ("Distancia (m)",    13),
        ("Ancho (m)",        11),
        ("Alto (m)",         11),
        ("Superficie (m2)",  14),
        ("Método detección", 16),
        ("Texto OCR",        30),
        ("Observaciones",    30),
        ("Parcela",          20),
        ("Dirección parcela",22),
        ("Prop. terreno",    22),
        ("CUIT prop. terreno",18),
        ("Prop. cartel",     22),
        ("CUIT prop. cartel",18),
        ("Publicidad actual",22),
        ("Lat",              14),
        ("Lon",              14),
        ("Estado proc.",     14),
    ]

    for col_idx, (titulo, ancho) in enumerate(columnas, start=1):
        celda = ws.cell(row=1, column=col_idx, value=titulo)
        celda.font      = font_header
        celda.fill      = fill_header
        celda.alignment = alin_centro
        ws.column_dimensions[get_column_letter(col_idx)].width = ancho

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"

    # ── Datos ─────────────────────────────────────────────────────────────────
    for fila_idx, c in enumerate(queryset, start=2):
        fill = fill_par if fila_idx % 2 == 0 else fill_impar

        pub = c.publicidad_actual()

        valores = [
            c.id,
            c.kobo_id or "",
            c.fecha.strftime("%d/%m/%Y %H:%M") if c.fecha else "",
            c.operador or "",
            c.get_tipo_cartel_display() if c.tipo_cartel else "",
            c.get_estado_cartel_display() if c.estado_cartel else "",
            c.distancia or "",
            c.ancho_m or "",
            c.alto_m or "",
            c.superficie_m2 or "",
            "",  # método detección — no en modelo, se puede agregar si se guarda
            c.texto_ocr or "",
            c.observaciones or "",
            c.parcela.nomenclatura() if c.parcela else "",
            c.parcela.direccion if c.parcela else "",
            c.parcela.propietario_terreno.nombre_completo() if c.parcela and c.parcela.propietario_terreno else "",
            c.parcela.propietario_terreno.cuit_dni if c.parcela and c.parcela.propietario_terreno else "",
            c.propietario_cartel.nombre_completo() if c.propietario_cartel else "",
            c.propietario_cartel.cuit_dni if c.propietario_cartel else "",
            pub.nombre_empresa() if pub else "",
            c.lat or "",
            c.lon or "",
            c.get_estado_procesamiento_display() if c.estado_procesamiento else "",
        ]

        for col_idx, valor in enumerate(valores, start=1):
            celda = ws.cell(row=fila_idx, column=col_idx, value=valor)
            celda.fill      = fill
            celda.alignment = alin_izq
            celda.font      = Font(size=9)

        ws.row_dimensions[fila_idx].height = 18

    # ── Resumen en hoja 2 ─────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Resumen")
    ws2["A1"] = "Generado el"
    ws2["B1"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    ws2["A2"] = "Total registros"
    ws2["B2"] = queryset.count()

    from django.db.models import Sum
    sup = queryset.aggregate(t=Sum("superficie_m2"))["t"]
    ws2["A3"] = "Superficie total (m2)"
    ws2["B3"] = round(sup, 2) if sup else 0

    for celda in ["A1", "A2", "A3"]:
        ws2[celda].font = Font(bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── PDF ───────────────────────────────────────────────────────────────────────

def exportar_pdf(queryset, media_root: str) -> bytes:
    """
    Genera un PDF con tabla de carteles y miniaturas de foto.
    Devuelve los bytes del archivo .pdf.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm,  bottomMargin=1.5*cm,
    )

    styles = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle(
        "titulo",
        parent=styles["Heading1"],
        fontSize=14,
        spaceAfter=6,
        textColor=colors.HexColor("#1E3A5F"),
    )
    estilo_subtitulo = ParagraphStyle(
        "subtitulo",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.grey,
        spaceAfter=12,
    )
    estilo_celda = ParagraphStyle(
        "celda",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=10,
    )
    estilo_celda_bold = ParagraphStyle(
        "celdabold",
        parent=estilo_celda,
        fontName="Helvetica-Bold",
    )
    estilo_header = ParagraphStyle(
        "header",
        parent=estilo_celda,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    story = []

    # ── Título ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Relevamiento de Carteles Publicitarios", estilo_titulo))
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  "
        f"{queryset.count()} registro(s)",
        estilo_subtitulo,
    ))

    # ── Encabezados de tabla ──────────────────────────────────────────────────
    COLOR_HEADER = colors.HexColor("#1E3A5F")
    COLOR_FILA_PAR = colors.HexColor("#EEF2F7")

    encabezados = [
        Paragraph("Foto",          estilo_header),
        Paragraph("#",             estilo_header),
        Paragraph("Tipo / Estado", estilo_header),
        Paragraph("Parcela",       estilo_header),
        Paragraph("Prop. terreno", estilo_header),
        Paragraph("Prop. cartel",  estilo_header),
        Paragraph("Publicidad",    estilo_header),
        Paragraph("Superficie",    estilo_header),
        Paragraph("Texto OCR",     estilo_header),
        Paragraph("Fecha",         estilo_header),
    ]

    # Anchos de columna en cm (total ~25.7cm en A4 landscape con márgenes)
    col_widths = [2.2, 1.0, 2.8, 3.2, 3.2, 3.2, 3.0, 2.0, 4.5, 2.0]

    filas = [encabezados]

    for c in queryset:
        # Miniatura
        img_cell = ""
        if c.foto_anotada and os.path.isfile(c.foto_anotada.path):
            try:
                img_cell = Image(c.foto_anotada.path, width=1.8*cm, height=1.3*cm)
            except Exception:
                img_cell = Paragraph("—", estilo_celda)
        elif c.foto and os.path.isfile(c.foto.path):
            try:
                img_cell = Image(c.foto.path, width=1.8*cm, height=1.3*cm)
            except Exception:
                img_cell = Paragraph("—", estilo_celda)
        else:
            img_cell = Paragraph("Sin foto", estilo_celda)

        pub = c.publicidad_actual()

        tipo_estado = f"{c.get_tipo_cartel_display() or '—'}\n{c.get_estado_cartel_display() or '—'}"
        parcela_txt = ""
        if c.parcela:
            parcela_txt = c.parcela.nomenclatura()
            if c.parcela.direccion:
                parcela_txt += f"\n{c.parcela.direccion}"

        prop_terreno = ""
        if c.parcela and c.parcela.propietario_terreno:
            pt = c.parcela.propietario_terreno
            prop_terreno = f"{pt.nombre_completo()}\n{pt.cuit_dni}"

        prop_cartel = ""
        if c.propietario_cartel:
            pc = c.propietario_cartel
            prop_cartel = f"{pc.nombre_completo()}\n{pc.cuit_dni}"

        superficie = f"{c.superficie_m2:.2f} m2" if c.superficie_m2 else "—"
        fecha_txt  = c.fecha.strftime("%d/%m/%Y") if c.fecha else "—"
        ocr_txt    = (c.texto_ocr or "")[:80]

        fila = [
            img_cell,
            Paragraph(str(c.id), estilo_celda),
            Paragraph(tipo_estado, estilo_celda),
            Paragraph(parcela_txt, estilo_celda),
            Paragraph(prop_terreno, estilo_celda),
            Paragraph(prop_cartel, estilo_celda),
            Paragraph(pub.nombre_empresa() if pub else "—", estilo_celda),
            Paragraph(superficie, estilo_celda_bold),
            Paragraph(ocr_txt, estilo_celda),
            Paragraph(fecha_txt, estilo_celda),
        ]
        filas.append(fila)

    # ── Estilos de tabla ──────────────────────────────────────────────────────
    n_filas = len(filas)
    estilo_tabla = TableStyle([
        # Encabezado
        ("BACKGROUND",  (0, 0), (-1, 0), COLOR_HEADER),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 8),
        ("ROWBACKGROUND", (0, 0), (-1, 0), COLOR_HEADER),
        # Filas pares
        *[("BACKGROUND", (0, i), (-1, i), COLOR_FILA_PAR)
          for i in range(2, n_filas, 2)],
        # General
        ("GRID",        (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ROWHEIGHT",   (0, 1), (-1, -1), 38),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",(0, 0), (-1, -1), 4),
    ])

    tabla = Table(filas, colWidths=[w*cm for w in col_widths], repeatRows=1)
    tabla.setStyle(estilo_tabla)

    story.append(tabla)

    # ── Pie de página con número ──────────────────────────────────────────────
    def pie_pagina(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(
            landscape(A4)[0] - 1.5*cm,
            0.8*cm,
            f"Página {doc.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
    return buf.getvalue()