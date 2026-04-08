"""
cartel_detector.py

Detecta el cartel principal en una imagen y calcula su superficie real.

PROTOCOLO DE CAPTURA ESPERADO:
  - El operador encuadra el cartel completo SIN zoom desde la distancia medida
  - El cartel ocupa la mayor parte del encuadre (50-90% del ancho o alto)
  - La distancia ingresada en el formulario es la distancia real al cartel

ESTRATEGIA DE DETECCIÓN (cascada):
  1. YOLO fine-tuneado (carteles_yolo.pt): modelo entrenado específicamente
     en billboards y traffic panels. Método principal desde v2.
  2. GrabCut: fallback si YOLO no detecta nada. Separa primer plano del fondo.
  3. Contornos rectangulares OpenCV: último recurso para casos con bordes definidos.

CÁLCULO DE SUPERFICIE:
  Semejanza de triángulos con FOV derivado del EXIF (FocalLengthIn35mmFilm).
  Fallback a 65° si no hay datos EXIF (FOV típico smartphone sin zoom).

VALIDACIÓN DE ZOOM:
  Si el ancho calculado del cartel queda fuera de 5cm–25m, avisa al operador.
"""

import math
import os

import cv2
import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS
from ultralytics import YOLO
from carteles.servicios.ocr_cartel import extraer_texto

# ── Modelo YOLO ──────────────────────────────────────────────────────────────
_MODELO_PATH = os.path.join(os.path.dirname(__file__), "carteles_yolo.pt")
_modelo = None

# Umbral de confianza alto porque el modelo es específico para carteles
_CONFIANZA_MINIMA_YOLO = 0.30


def _get_modelo():
    global _modelo
    if _modelo is None:
        _modelo = YOLO(_MODELO_PATH)
        try:
            import torch
            if torch.cuda.is_available():
                _modelo.to("cuda")  # usar GPU RTX 3050
            else:
                _modelo.to("cpu")
        except Exception:
            _modelo.to("cpu")
    return _modelo


# ── EXIF ─────────────────────────────────────────────────────────────────────

def _leer_exif(ruta_imagen: str) -> dict:
    datos = {"focal_mm": None, "focal_35mm": None, "digital_zoom": None,
             "ancho_px": None, "alto_px": None,
             "make": "", "model": ""}
    try:
        img = Image.open(ruta_imagen)
        datos["ancho_px"] = img.width
        datos["alto_px"] = img.height
        exif = img._getexif()
        if not exif:
            return datos
        for tag_id, valor in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "FocalLength":
                if hasattr(valor, "numerator"):
                    datos["focal_mm"] = float(valor.numerator) / float(valor.denominator)
                elif isinstance(valor, tuple):
                    datos["focal_mm"] = float(valor[0]) / float(valor[1])
                else:
                    datos["focal_mm"] = float(valor)
            elif tag == "FocalLengthIn35mmFilm":
                datos["focal_35mm"] = float(valor)
            elif tag == "DigitalZoomRatio":
                try:
                    datos["digital_zoom"] = (
                        float(valor.numerator) / float(valor.denominator)
                        if hasattr(valor, "numerator") else float(valor)
                    )
                except Exception:
                    pass
            elif tag == "Make":
                datos["make"] = str(valor).strip().lower()
            elif tag == "Model":
                datos["model"] = str(valor).strip().lower()
    except Exception:
        pass
    return datos


# ── FOV ──────────────────────────────────────────────────────────────────────

# Tabla de FOV horizontal real por modelo de celular.
# Usar cuando el EXIF no trae FocalLengthIn35mmFilm.
# FOV = 2 * atan(18 / focal_35mm_equiv)
# Agregar nuevos modelos según se incorporen al relevamiento.
_FOV_POR_MODELO = {
    "moto g56 5g": 69.4,   # 26mm equiv → 2*atan(18/26) = 69.4°
    "moto g56":    69.4,
}


def _calcular_fov_h(exif: dict) -> float:
    """
    FOV horizontal en grados.
    Prioridad:
      1. FocalLengthIn35mmFilm del EXIF (más preciso)
      2. FocalLength real + resolución estimada del sensor
      3. Tabla de FOV por modelo de celular
      4. Fallback genérico 65°
    """
    # 1. FocalLengthIn35mmFilm
    focal_35mm = exif.get("focal_35mm")
    if focal_35mm and focal_35mm > 0:
        fov = 2 * math.degrees(math.atan(18.0 / focal_35mm))
        if 20 < fov < 120:
            return fov

    # 2. FocalLength real + tamaño de sensor estimado
    focal_mm = exif.get("focal_mm")
    ancho_px = exif.get("ancho_px")
    if focal_mm and focal_mm > 0 and ancho_px:
        sensor_w = ancho_px * 0.001
        fov = 2 * math.degrees(math.atan(sensor_w / (2 * focal_mm)))
        if 20 < fov < 120:
            return fov

    # 3. Tabla por modelo de celular
    model = exif.get("model", "").lower()
    for patron, fov_modelo in _FOV_POR_MODELO.items():
        if patron in model:
            return fov_modelo

    # 4. Fallback genérico
    return 65.0


def _fov_v(fov_h: float, ancho_px: int, alto_px: int) -> float:
    return 2 * math.degrees(
        math.atan(math.tan(math.radians(fov_h / 2)) * alto_px / ancho_px)
    )


# ── Conversión px → metros ───────────────────────────────────────────────────

def _px_a_m(px: int, px_total: int, dist_m: float, fov_deg: float) -> float:
    total_m = 2 * dist_m * math.tan(math.radians(fov_deg / 2))
    return (px / px_total) * total_m


# ── Método 1: YOLO fine-tuneado (principal) ───────────────────────────────────

def _detectar_yolo(ruta_imagen: str, alto_img: int, ancho_img: int) -> dict | None:
    """
    Detecta usando el modelo entrenado específicamente en billboards.
    Filtra solo la clase 0 (billboard) ya que es la relevante para el sistema.
    Si hay varias detecciones elige la de mayor confianza.
    """
    modelo = _get_modelo()
    resultados = modelo(ruta_imagen, verbose=False, classes=[0])  # 0 = billboard
    area_img = alto_img * ancho_img
    mejor = None
    mejor_conf = 0

    for r in resultados:
        if r.boxes is None:
            continue
        for box in r.boxes:
            conf = float(box.conf[0])
            if conf < _CONFIANZA_MINIMA_YOLO:
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            w, h = x2 - x1, y2 - y1
            if w == 0 or h == 0:
                continue
            area_ratio = (w * h) / area_img
            if area_ratio < 0.05 or area_ratio > 0.95:
                continue
            if conf > mejor_conf:
                mejor_conf = conf
                mejor = {"x": x1, "y": y1, "w": w, "h": h, "confianza": round(conf, 4)}

    return mejor


# ── Método 2: GrabCut (fallback) ─────────────────────────────────────────────

def _detectar_grabcut(imagen: np.ndarray) -> dict | None:
    """
    Fallback: usa GrabCut para separar primer plano del fondo.
    Elige el bbox más grande con proporción razonable de cartel.
    """
    alto, ancho = imagen.shape[:2]
    area_img = alto * ancho

    mask = np.zeros((alto, ancho), np.uint8)
    rect = (
        int(ancho * 0.05), int(alto * 0.05),
        int(ancho * 0.90), int(alto * 0.90)
    )
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)

    try:
        cv2.grabCut(imagen, mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    except Exception:
        return None

    mask2 = np.where((mask == 2) | (mask == 0), 0, 255).astype("uint8")
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    mask2 = cv2.morphologyEx(mask2, cv2.MORPH_CLOSE, kernel)

    contornos, _ = cv2.findContours(mask2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None

    candidatos = []
    for cnt in contornos:
        x, y, w, h = cv2.boundingRect(cnt)
        if w == 0 or h == 0:
            continue
        area_ratio = (w * h) / area_img
        if area_ratio < 0.08 or area_ratio > 0.95:
            continue
        asp = w / h
        if asp < 0.3 or asp > 7.0:
            continue
        candidatos.append((area_ratio, x, y, w, h))

    if not candidatos:
        return None

    candidatos.sort(reverse=True)
    _, x, y, w, h = candidatos[0]
    return {"x": x, "y": y, "w": w, "h": h, "confianza": round(candidatos[0][0], 4)}


# ── Método 3: Contornos rectangulares (último recurso) ───────────────────────

def _detectar_contornos(imagen: np.ndarray) -> dict | None:
    alto, ancho = imagen.shape[:2]
    area_img = alto * ancho
    cx_img, cy_img = ancho / 2, alto / 2
    diag = math.sqrt(ancho ** 2 + alto ** 2)

    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gris, (7, 7), 0)
    edges = cv2.Canny(blur, 30, 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contornos, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mejor = None
    mejor_score = 0

    for cnt in contornos:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)
        if len(approx) != 4:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        if w == 0 or h == 0:
            continue
        area_ratio = (w * h) / area_img
        if area_ratio < 0.08 or area_ratio > 0.92:
            continue
        if max(w, h) / min(w, h) > 6.0:
            continue
        cx = x + w / 2
        cy = y + h / 2
        dist_c = math.sqrt((cx - cx_img) ** 2 + (cy - cy_img) ** 2)
        centralidad = 1 - (dist_c / (diag / 2))
        score = area_ratio * (0.5 + 0.5 * centralidad)
        if score > mejor_score:
            mejor_score = score
            mejor = {"x": x, "y": y, "w": w, "h": h, "confianza": round(score, 4)}

    return mejor


# ── Validación de zoom ────────────────────────────────────────────────────────

def _zoom_sospechoso(bbox: dict, ancho_total_px: int, dist_m: float, fov_h: float) -> bool:
    ancho_calculado = _px_a_m(bbox["w"], ancho_total_px, dist_m, fov_h)
    return ancho_calculado < 0.05 or ancho_calculado > 25.0


# ── Función principal ─────────────────────────────────────────────────────────

def detectar_cartel(ruta_imagen: str, distancia_m: float) -> dict:
    resultado = {
        "detectado": False, "confianza": None, "bbox": None,
        "ancho_m": None, "alto_m": None, "superficie_m2": None,
        "foto_anotada_array": None, "metodo_deteccion": None,
        "fov_usado": None, "zoom_sospechoso": False,
        "texto_ocr": None, "sin_texto": False, "error": None,
    }

    if not distancia_m or distancia_m <= 0:
        resultado["error"] = "distancia_invalida"
        return resultado

    imagen = cv2.imread(ruta_imagen)
    if imagen is None:
        resultado["error"] = "imagen_ilegible"
        return resultado

    alto_total, ancho_total = imagen.shape[:2]

    exif = _leer_exif(ruta_imagen)
    fov_h = _calcular_fov_h(exif)

    zoom = exif.get("digital_zoom")
    if zoom and zoom > 1.0:
        fov_h = fov_h / zoom

    fov_v_deg = _fov_v(fov_h, ancho_total, alto_total)

    # ── Detección en cascada: YOLO → GrabCut → Contornos ────────────────────
    bbox = _detectar_yolo(ruta_imagen, alto_total, ancho_total)
    metodo = "yolo"

    if bbox is None:
        bbox = _detectar_grabcut(imagen)
        metodo = "grabcut"

    if bbox is None:
        bbox = _detectar_contornos(imagen)
        metodo = "contornos"

    if bbox is None:
        resultado["error"] = "sin_deteccion"
        return resultado

    # ── Cálculo de superficie ────────────────────────────────────────────────
    ancho_m = _px_a_m(bbox["w"], ancho_total, distancia_m, fov_h)
    alto_m  = _px_a_m(bbox["h"], alto_total,  distancia_m, fov_v_deg)
    superficie_m2 = round(ancho_m * alto_m, 4)
    ancho_m = round(ancho_m, 4)
    alto_m  = round(alto_m,  4)

    zoom_flag = _zoom_sospechoso(bbox, ancho_total, distancia_m, fov_h)

    # ── Imagen anotada ───────────────────────────────────────────────────────
    img_out = imagen.copy()
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    color = (0, 165, 255) if zoom_flag else (0, 220, 0)
    grosor = max(2, int(min(alto_total, ancho_total)*0.002))
    cv2.rectangle(img_out, (x, y), (x + w, y + h), color, grosor)
    escala = max(0.9, min(2.0, ancho_total) / 1000)
    cv2.putText(img_out,
                f"{ancho_m:.2f}m x {alto_m:.2f}m = {superficie_m2:.2f}m2",
                (x, max(y - 12, 30)),
                cv2.FONT_HERSHEY_SIMPLEX, escala, color, grosor)
    aviso = "⚠ ZOOM/DIST?" if zoom_flag else ""
    cv2.putText(img_out,
                f"[{metodo}] FOV:{fov_h:.1f}° dist:{distancia_m}m {aviso}",
                (10, alto_total - 15),
                cv2.FONT_HERSHEY_SIMPLEX, escala, (255, 255, 0), grosor)

    # ── OCR sobre el recorte del bbox ────────────────────────────────────────
    ocr = extraer_texto(ruta_imagen, bbox)

    resultado.update({
        "detectado": True,
        "confianza": bbox.get("confianza"),
        "bbox": bbox,
        "ancho_m": ancho_m,
        "alto_m": alto_m,
        "superficie_m2": superficie_m2,
        "foto_anotada_array": img_out,
        "metodo_deteccion": metodo,
        "fov_usado": round(fov_h, 2),
        "zoom_sospechoso": zoom_flag,
        "texto_ocr": ocr.get("texto"),
        "sin_texto": ocr.get("sin_texto", False),
    })
    return resultado