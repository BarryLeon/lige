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


def _matriz_camara(ancho_px: int, alto_px: int, fov_h: float) -> np.ndarray:
    """Aproxima la matriz intrínseca de cámara a partir del FOV horizontal."""
    focal_px = ancho_px / (2 * math.tan(math.radians(fov_h / 2)))
    return np.array(
        [
            [focal_px, 0.0, ancho_px / 2],
            [0.0, focal_px, alto_px / 2],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _ordenar_esquinas(puntos: np.ndarray) -> np.ndarray:
    """Ordena cuatro esquinas como: sup-izq, sup-der, inf-der, inf-izq."""
    puntos = np.asarray(puntos, dtype=np.float32).reshape(4, 2)
    suma = puntos.sum(axis=1)
    diferencia = np.diff(puntos, axis=1).reshape(-1)
    return np.array(
        [
            puntos[np.argmin(suma)],
            puntos[np.argmin(diferencia)],
            puntos[np.argmax(suma)],
            puntos[np.argmax(diferencia)],
        ],
        dtype=np.float32,
    )


def _normalizar_esquinas_manuales(esquinas: list | np.ndarray | None) -> np.ndarray | None:
    """Valida y normaliza cuatro esquinas manuales en coordenadas de imagen."""
    if not esquinas:
        return None
    try:
        if isinstance(esquinas, list) and esquinas and isinstance(esquinas[0], dict):
            arr = np.asarray(
                [[p["x"], p["y"]] for p in esquinas],
                dtype=np.float32,
            ).reshape(4, 2)
        else:
            arr = np.asarray(esquinas, dtype=np.float32).reshape(4, 2)
    except Exception:
        return None
    if not np.all(np.isfinite(arr)):
        return None
    return _ordenar_esquinas(arr)


def _bbox_desde_esquinas(esquinas: np.ndarray, ancho_total: int, alto_total: int) -> dict | None:
    xs = np.clip(esquinas[:, 0], 0, ancho_total - 1)
    ys = np.clip(esquinas[:, 1], 0, alto_total - 1)
    x1, x2 = int(np.floor(xs.min())), int(np.ceil(xs.max()))
    y1, y2 = int(np.floor(ys.min())), int(np.ceil(ys.max()))
    w = x2 - x1
    h = y2 - y1
    if w <= 1 or h <= 1:
        return None
    return {"x": x1, "y": y1, "w": w, "h": h, "confianza": 1.0}


def _angulo_segmento(linea: np.ndarray) -> float:
    x1, y1, x2, y2 = map(float, linea)
    return (math.degrees(math.atan2(y2 - y1, x2 - x1)) + 180.0) % 180.0


def _longitud_segmento(linea: np.ndarray) -> float:
    x1, y1, x2, y2 = map(float, linea)
    return math.hypot(x2 - x1, y2 - y1)


def _linea_homogenea(linea: np.ndarray) -> np.ndarray | None:
    x1, y1, x2, y2 = map(float, linea)
    a = y1 - y2
    b = x2 - x1
    c = x1 * y2 - x2 * y1
    norma = math.hypot(a, b)
    if norma <= 1e-6:
        return None
    a, b, c = a / norma, b / norma, c / norma
    if a < 0 or (abs(a) <= 1e-6 and b < 0):
        a, b, c = -a, -b, -c
    return np.array([a, b, c], dtype=np.float64)


def _interseccion_lineas(linea_a: np.ndarray, linea_b: np.ndarray) -> np.ndarray | None:
    punto = np.cross(linea_a, linea_b)
    if abs(punto[2]) <= 1e-6:
        return None
    return (punto[:2] / punto[2]).astype(np.float32)


def _score_cuadrilatero(
    quad_local: np.ndarray,
    quad_global: np.ndarray,
    bbox: dict,
    ancho_roi: int,
    alto_roi: int,
) -> float:
    area_quad = cv2.contourArea(quad_global.astype(np.float32))
    area_bbox = float(bbox["w"] * bbox["h"])
    if area_quad < area_bbox * 0.20:
        return -1.0

    ancho_sup = np.linalg.norm(quad_global[1] - quad_global[0])
    ancho_inf = np.linalg.norm(quad_global[2] - quad_global[3])
    alto_izq = np.linalg.norm(quad_global[3] - quad_global[0])
    alto_der = np.linalg.norm(quad_global[2] - quad_global[1])
    if min(ancho_sup, ancho_inf, alto_izq, alto_der) < 20:
        return -1.0

    ratio = max(ancho_sup, ancho_inf) / max(min(alto_izq, alto_der), 1e-6)
    if ratio < 0.2 or ratio > 8.0:
        return -1.0

    if (
        np.any(quad_local[:, 0] < -0.12 * ancho_roi)
        or np.any(quad_local[:, 0] > 1.12 * ancho_roi)
        or np.any(quad_local[:, 1] < -0.12 * alto_roi)
        or np.any(quad_local[:, 1] > 1.12 * alto_roi)
    ):
        return -1.0

    x, y, w, h = cv2.boundingRect(quad_global.astype(np.int32))
    inter_x1 = max(x, bbox["x"])
    inter_y1 = max(y, bbox["y"])
    inter_x2 = min(x + w, bbox["x"] + bbox["w"])
    inter_y2 = min(y + h, bbox["y"] + bbox["h"])
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    union_area = (w * h) + area_bbox - inter_area
    iou = inter_area / union_area if union_area else 0.0

    relleno = area_quad / area_bbox
    asimetria = (
        abs(ancho_sup - ancho_inf) / max(ancho_sup, ancho_inf)
        + abs(alto_izq - alto_der) / max(alto_izq, alto_der)
    )
    return (relleno * 0.70) + (iou * 0.20) - (asimetria * 0.15)


def _detectar_cuadrilatero_por_lineas(imagen: np.ndarray, bbox: dict) -> np.ndarray | None:
    """
    Reconstruye el cartel a partir de líneas largas dominantes.

    Funciona mejor que GrabCut cuando el borde del cartel es bien recto pero el
    interior tiene mucho contraste o elementos gráficos que confunden la segmentación.
    """
    alto_total, ancho_total = imagen.shape[:2]
    margen = max(20, int(min(bbox["w"], bbox["h"]) * 0.08))

    x0 = max(0, bbox["x"] - margen)
    y0 = max(0, bbox["y"] - margen)
    x1 = min(ancho_total, bbox["x"] + bbox["w"] + margen)
    y1 = min(alto_total, bbox["y"] + bbox["h"] + margen)
    roi = imagen[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    gris = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gris, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    min_linea = max(40, int(min(roi.shape[:2]) * 0.35))
    lineas = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=80,
        minLineLength=min_linea,
        maxLineGap=30,
    )
    if lineas is None:
        return None

    horizontales = []
    verticales = []
    for linea in lineas[:, 0, :]:
        angulo = _angulo_segmento(linea)
        longitud = _longitud_segmento(linea)
        hom = _linea_homogenea(linea)
        if hom is None:
            continue
        item = {
            "linea": linea,
            "angulo": angulo,
            "longitud": longitud,
            "hom": hom,
        }
        if angulo <= 25 or angulo >= 155:
            horizontales.append(item)
        elif 65 <= angulo <= 115:
            verticales.append(item)

    horizontales.sort(key=lambda item: item["longitud"], reverse=True)
    verticales.sort(key=lambda item: item["longitud"], reverse=True)
    horizontales = horizontales[:14]
    verticales = verticales[:14]

    mejor = None
    mejor_score = -1.0

    for i in range(len(horizontales)):
        for j in range(i + 1, len(horizontales)):
            h1 = horizontales[i]
            h2 = horizontales[j]
            for k in range(len(verticales)):
                for l in range(k + 1, len(verticales)):
                    v1 = verticales[k]
                    v2 = verticales[l]
                    puntos = [
                        _interseccion_lineas(h1["hom"], v1["hom"]),
                        _interseccion_lineas(h1["hom"], v2["hom"]),
                        _interseccion_lineas(h2["hom"], v2["hom"]),
                        _interseccion_lineas(h2["hom"], v1["hom"]),
                    ]
                    if any(p is None for p in puntos):
                        continue
                    quad_local = _ordenar_esquinas(np.array(puntos, dtype=np.float32))
                    quad_global = quad_local + np.array([x0, y0], dtype=np.float32)
                    score = _score_cuadrilatero(quad_local, quad_global, bbox, roi.shape[1], roi.shape[0])
                    if score > mejor_score:
                        mejor_score = score
                        mejor = quad_global

    return mejor


def _detectar_cuadrilatero_cartel(imagen: np.ndarray, bbox: dict) -> np.ndarray | None:
    """
    Busca un cuadrilátero dentro del bbox detectado.

    Prioriza el contorno principal extraído con GrabCut local y cae a un rectángulo
    mínimo rotado cuando no logra una aproximación de 4 vértices limpia.
    """
    mejor_lineas = _detectar_cuadrilatero_por_lineas(imagen, bbox)
    if mejor_lineas is not None:
        return mejor_lineas

    alto_total, ancho_total = imagen.shape[:2]
    margen = max(12, int(min(bbox["w"], bbox["h"]) * 0.06))

    x0 = max(0, bbox["x"] - margen)
    y0 = max(0, bbox["y"] - margen)
    x1 = min(ancho_total, bbox["x"] + bbox["w"] + margen)
    y1 = min(alto_total, bbox["y"] + bbox["h"] + margen)

    roi = imagen[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    mask = np.zeros(roi.shape[:2], np.uint8)
    rect = (
        bbox["x"] - x0,
        bbox["y"] - y0,
        min(bbox["w"], roi.shape[1] - 1),
        min(bbox["h"], roi.shape[0] - 1),
    )

    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)

    try:
        cv2.grabCut(roi, mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)
    except Exception:
        return None

    mask2 = np.where((mask == 2) | (mask == 0), 0, 255).astype("uint8")
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))
    mask2 = cv2.morphologyEx(mask2, cv2.MORPH_CLOSE, kernel)

    contornos, _ = cv2.findContours(mask2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None

    area_bbox = float(bbox["w"] * bbox["h"])
    mejor = None
    mejor_score = 0.0

    for cnt in contornos:
        area = cv2.contourArea(cnt)
        if area < area_bbox * 0.20:
            continue

        peri = cv2.arcLength(cnt, True)
        approx = None
        for eps_ratio in (0.015, 0.02, 0.03, 0.04, 0.05):
            candidato = cv2.approxPolyDP(cnt, eps_ratio * peri, True)
            if len(candidato) == 4:
                approx = candidato.reshape(4, 2)
                break

        if approx is None:
            hull = cv2.convexHull(cnt)
            rect_rot = cv2.minAreaRect(hull)
            approx = cv2.boxPoints(rect_rot)

        quad = _ordenar_esquinas(approx)
        quad_global = quad + np.array([x0, y0], dtype=np.float32)
        score = _score_cuadrilatero(quad, quad_global, bbox, roi.shape[1], roi.shape[0])
        if score > mejor_score:
            mejor_score = score
            mejor = quad_global

    return mejor


def _conviene_usar_homografia(esquinas: np.ndarray, bbox: dict) -> bool:
    """
    Usa homografía solo cuando el cartel muestra perspectiva apreciable.

    En tomas casi frontales, el cálculo simple por bbox suele ser más estable.
    """
    quad = _ordenar_esquinas(esquinas)
    ancho_sup = np.linalg.norm(quad[1] - quad[0])
    ancho_inf = np.linalg.norm(quad[2] - quad[3])
    alto_izq = np.linalg.norm(quad[3] - quad[0])
    alto_der = np.linalg.norm(quad[2] - quad[1])

    if min(ancho_sup, ancho_inf, alto_izq, alto_der) <= 1:
        return False

    desbalance_ancho = abs(ancho_sup - ancho_inf) / max(ancho_sup, ancho_inf)
    desbalance_alto = abs(alto_izq - alto_der) / max(alto_izq, alto_der)
    area_quad = cv2.contourArea(quad.astype(np.float32))
    area_bbox = float(bbox["w"] * bbox["h"])
    relleno = area_quad / area_bbox if area_bbox else 0.0

    return desbalance_ancho > 0.10 or desbalance_alto > 0.12 or relleno < 0.82


def _estimar_superficie_por_homografia(
    esquinas: np.ndarray,
    ancho_total: int,
    alto_total: int,
    fov_h: float,
    distancia_m: float,
) -> dict | None:
    """
    Estima ancho, alto y superficie reales suponiendo que el cartel es rectangular.

    Usa la homografía entre el cuadrilátero detectado y un rectángulo canónico para
    recuperar proporción, pose relativa y escalarla con la distancia ingresada.
    """
    if distancia_m <= 0:
        return None

    esquinas = _ordenar_esquinas(esquinas)
    K = _matriz_camara(ancho_total, alto_total, fov_h)

    cuadrado = np.array(
        [[0, 0], [1, 0], [1, 1], [0, 1]],
        dtype=np.float32,
    )
    H = cv2.getPerspectiveTransform(cuadrado, esquinas.astype(np.float32))
    G = np.linalg.inv(K) @ H
    g1, g2 = G[:, 0], G[:, 1]

    norma_g1 = np.linalg.norm(g1)
    norma_g2 = np.linalg.norm(g2)
    if norma_g1 <= 1e-6 or norma_g2 <= 1e-6:
        return None

    proporcion = float(norma_g1 / norma_g2)
    if not (0.2 <= proporcion <= 8.0):
        return None

    obj_pts = np.array(
        [[0, 0, 0], [proporcion, 0, 0], [proporcion, 1, 0], [0, 1, 0]],
        dtype=np.float64,
    )
    img_pts = esquinas.astype(np.float64)

    ok, rvec, tvec = cv2.solvePnP(
        obj_pts,
        img_pts,
        K,
        None,
        flags=cv2.SOLVEPNP_IPPE,
    )
    if not ok:
        ok, rvec, tvec = cv2.solvePnP(
            obj_pts,
            img_pts,
            K,
            None,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
    if not ok:
        return None

    rotacion, _ = cv2.Rodrigues(rvec)
    centro_modelo = np.array([[proporcion / 2], [0.5], [0]], dtype=np.float64)
    centro_camara = rotacion @ centro_modelo + tvec
    distancia_modelo = float(np.linalg.norm(centro_camara))
    if distancia_modelo <= 1e-6:
        return None

    alto_m = distancia_m / distancia_modelo
    ancho_m = proporcion * alto_m
    superficie_m2 = ancho_m * alto_m

    if not all(math.isfinite(v) for v in (ancho_m, alto_m, superficie_m2)):
        return None
    if min(ancho_m, alto_m, superficie_m2) <= 0:
        return None

    return {
        "ancho_m": round(ancho_m, 4),
        "alto_m": round(alto_m, 4),
        "superficie_m2": round(superficie_m2, 4),
        "proporcion": round(proporcion, 4),
        "esquinas": esquinas,
    }


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


def _diagnosticar_consistencia_superficie(
    metodo_superficie: str,
    superficie_bbox_m2: float,
    superficie_homografia_m2: float | None,
    zoom_flag: bool,
) -> tuple[bool, str | None]:
    """
    Genera una advertencia cuando la geometría de la imagen y la medición simple
    por bbox divergen demasiado.
    """
    if zoom_flag:
        return (
            True,
            "La geometría detectada produce medidas poco confiables. Revisá que la foto "
            "haya sido tomada sin zoom y que la distancia cargada sea la real.",
        )

    if metodo_superficie == "homografia" and superficie_homografia_m2:
        diferencia_rel = abs(superficie_homografia_m2 - superficie_bbox_m2) / max(
            superficie_homografia_m2,
            superficie_bbox_m2,
            1e-6,
        )
        if diferencia_rel >= 0.18:
            return (
                True,
                f"La estimación por homografía difiere {diferencia_rel * 100:.0f}% de la "
                "estimación simple por bounding box. Conviene revisar la distancia medida "
                "y, si hace falta, corregirla y recalcular.",
            )
        return (
            True,
            "La foto presenta perspectiva marcada y la superficie se calculó con homografía. "
            "Si la medida no cierra con lo observado en campo, corregí la distancia y recalculá.",
        )

    return False, None


# ── Función principal ─────────────────────────────────────────────────────────

def detectar_cartel(
    ruta_imagen: str,
    distancia_m: float,
    esquinas_manuales: list | np.ndarray | None = None,
) -> dict:
    resultado = {
        "detectado": False, "confianza": None, "bbox": None,
        "ancho_m": None, "alto_m": None, "superficie_m2": None,
        "foto_anotada_array": None, "metodo_deteccion": None,
        "fov_usado": None, "zoom_sospechoso": False,
        "texto_ocr": None, "sin_texto": False, "error": None,
        "metodo_superficie": "bbox",
        "origen_medicion": "automatica",
        "diagnostico_geometria_inconsistente": False,
        "detalle_diagnostico": None,
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

    esquinas = _normalizar_esquinas_manuales(esquinas_manuales)
    if esquinas is not None:
        bbox = _bbox_desde_esquinas(esquinas, ancho_total, alto_total)
        if bbox is None:
            resultado["error"] = "sin_deteccion"
            return resultado
        metodo = "manual"
        origen_medicion = "manual"
    else:
        # ── Detección en cascada: YOLO → GrabCut → Contornos ─────────────────
        bbox = _detectar_yolo(ruta_imagen, alto_total, ancho_total)
        metodo = "yolo"
        origen_medicion = "automatica"

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
    ancho_bbox_m = _px_a_m(bbox["w"], ancho_total, distancia_m, fov_h)
    alto_bbox_m = _px_a_m(bbox["h"], alto_total, distancia_m, fov_v_deg)
    superficie_bbox_m2 = round(ancho_bbox_m * alto_bbox_m, 4)

    ancho_m = round(ancho_bbox_m, 4)
    alto_m = round(alto_bbox_m, 4)
    superficie_m2 = superficie_bbox_m2
    metodo_superficie = "bbox"

    if esquinas is None:
        esquinas = _detectar_cuadrilatero_cartel(imagen, bbox)
    estimacion_h = None
    superficie_homografia_m2 = None
    usar_homografia = esquinas is not None and (
        origen_medicion == "manual" or _conviene_usar_homografia(esquinas, bbox)
    )
    if usar_homografia:
        estimacion_h = _estimar_superficie_por_homografia(
            esquinas,
            ancho_total,
            alto_total,
            fov_h,
            distancia_m,
        )
    if estimacion_h is not None:
        ancho_m = estimacion_h["ancho_m"]
        alto_m = estimacion_h["alto_m"]
        superficie_m2 = estimacion_h["superficie_m2"]
        superficie_homografia_m2 = estimacion_h["superficie_m2"]
        metodo_superficie = "homografia"

    zoom_flag = ancho_m < 0.05 or ancho_m > 25.0
    diagnostico_geometria_inconsistente, detalle_diagnostico = _diagnosticar_consistencia_superficie(
        metodo_superficie=metodo_superficie,
        superficie_bbox_m2=superficie_bbox_m2,
        superficie_homografia_m2=superficie_homografia_m2,
        zoom_flag=zoom_flag,
    )

    # ── Imagen anotada ───────────────────────────────────────────────────────
    img_out = imagen.copy()
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    color = (0, 165, 255) if zoom_flag else (0, 220, 0)
    grosor = max(2, int(min(alto_total, ancho_total)*0.002))
    cv2.rectangle(img_out, (x, y), (x + w, y + h), color, grosor)
    if estimacion_h is not None:
        puntos = estimacion_h["esquinas"].astype(np.int32).reshape((-1, 1, 2))
        cv2.polylines(img_out, [puntos], True, (255, 120, 0), grosor)
    escala = max(0.9, min(2.0, ancho_total) / 1000)
    cv2.putText(img_out,
                f"{ancho_m:.2f}m x {alto_m:.2f}m = {superficie_m2:.2f}m2",
                (x, max(y - 12, 30)),
                cv2.FONT_HERSHEY_SIMPLEX, escala, color, grosor)
    aviso = "⚠ ZOOM/DIST?" if zoom_flag else ""
    cv2.putText(img_out,
                f"[{metodo}/{metodo_superficie}] FOV:{fov_h:.1f}° dist:{distancia_m}m {aviso}",
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
        "metodo_superficie": metodo_superficie,
        "origen_medicion": origen_medicion,
        "fov_usado": round(fov_h, 2),
        "zoom_sospechoso": zoom_flag,
        "diagnostico_geometria_inconsistente": diagnostico_geometria_inconsistente,
        "detalle_diagnostico": detalle_diagnostico,
        "texto_ocr": ocr.get("texto"),
        "sin_texto": ocr.get("sin_texto", False),
    })
    return resultado
