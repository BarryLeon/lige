"""
ocr_cartel.py

Extrae el texto visible de un cartel usando EasyOCR sobre el recorte
exacto del bounding box detectado (sin fondo).

Dependencias: easyocr, opencv-python, numpy

EasyOCR descarga los modelos la primera vez (~100MB).
Se inicializa de forma lazy para no bloquear el arranque de Django.
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── Lector EasyOCR (lazy, singleton) ─────────────────────────────────────────
_lector = None


def _get_lector():
    global _lector
    if _lector is None:
        import easyocr
        import torch
        usa_gpu = torch.cuda.is_available()
        _lector = easyocr.Reader(["es", "en"], gpu=usa_gpu, verbose=False)
    return _lector


# ── Función principal ─────────────────────────────────────────────────────────

def extraer_texto(ruta_imagen: str, bbox: dict | None) -> dict:
    """
    Extrae el texto del cartel recortando el bbox de la imagen.

    Args:
        ruta_imagen: Ruta absoluta a la imagen original.
        bbox: dict con x, y, w, h en píxeles (resultado del detector).
              Si es None, se corre OCR sobre la imagen completa.

    Returns:
        dict con:
            texto        (str): texto extraído, líneas separadas por \\n
            fragmentos   (list): lista de dicts con text, confianza, bbox_ocr
            sin_texto    (bool): True si no se encontró nada legible
            error        (str | None): descripción del error si falla
    """
    resultado = {
        "texto": None,
        "fragmentos": [],
        "sin_texto": False,
        "error": None,
    }

    # 1. Cargar imagen
    imagen = cv2.imread(ruta_imagen)
    if imagen is None:
        resultado["error"] = "imagen_ilegible"
        return resultado

    alto, ancho = imagen.shape[:2]

    # 2. Recortar bbox con margen pequeño para no cortar letras del borde
    if bbox:
        MARGEN = 10
        x = max(0, bbox["x"] - MARGEN)
        y = max(0, bbox["y"] - MARGEN)
        x2 = min(ancho, bbox["x"] + bbox["w"] + MARGEN)
        y2 = min(alto,  bbox["y"] + bbox["h"] + MARGEN)
        recorte = imagen[y:y2, x:x2]
    else:
        recorte = imagen

    if recorte.size == 0:
        resultado["error"] = "recorte_vacio"
        return resultado

    # 3. Preprocesar el recorte para mejorar OCR
    #    - Escalar a mínimo 300px de alto (EasyOCR funciona mejor con imágenes grandes)
    alto_recorte = recorte.shape[0]
    if alto_recorte < 300:
        factor = 300 / alto_recorte
        recorte = cv2.resize(recorte, None, fx=factor, fy=factor,
                             interpolation=cv2.INTER_CUBIC)

    # 4. Correr EasyOCR
    try:
        lector = _get_lector()
        detecciones = lector.readtext(recorte, detail=1, paragraph=False)
    except Exception as exc:
        logger.error(f"Error en EasyOCR: {exc}")
        resultado["error"] = f"ocr_fallido: {exc}"
        return resultado

    # 5. Filtrar por confianza mínima y armar resultado
    CONFIANZA_MINIMA = 0.25
    fragmentos = []

    for det in detecciones:
        bbox_ocr, texto, confianza = det
        if confianza < CONFIANZA_MINIMA:
            continue
        if not texto.strip():
            continue
        fragmentos.append({
            "text": texto.strip(),
            "confianza": round(confianza, 3),
        })

    if not fragmentos:
        resultado["sin_texto"] = True
        resultado["texto"] = ""
        return resultado

    # 6. Ordenar fragmentos de arriba a abajo (por posición Y del bbox_ocr)
    detecciones_filtradas = [
        d for d in detecciones
        if d[2] >= CONFIANZA_MINIMA and d[1].strip()
    ]
    detecciones_filtradas.sort(key=lambda d: d[0][0][1])  # ordenar por Y superior

    lineas = [d[1].strip() for d in detecciones_filtradas]
    texto_completo = "\n".join(lineas)

    resultado["texto"] = texto_completo
    resultado["fragmentos"] = fragmentos
    return resultado