"""
importar_kobo.py
Importa registros desde KoboToolbox, descarga las fotos y dispara el detector.

Flujo:
    1. Consulta la API de Kobo y trae todos los registros.
    2. Salta los que ya existen en la BD (por kobo_id).
    3. Por cada registro nuevo:
        a. Crea el objeto Cartel con los datos del formulario.
        b. Si no hay foto → marca error_sin_foto y sigue.
        c. Si hay foto → la descarga y llama al detector.
        d. Guarda el resultado (superficie o error) en el Cartel.
    4. Devuelve un resumen del proceso.
"""

import logging
import os
import tempfile
from datetime import datetime, timezone

import requests
from django.core.files.base import ContentFile

from carteles.models import Cartel
from carteles.servicios.cartel_detector import detectar_cartel

logger = logging.getLogger(__name__)

# ── Configuración de Kobo ────────────────────────────────────────────────────
# IMPORTANTE: en producción mover estos valores a variables de entorno o
# a settings.py para no exponer credenciales en el código fuente.
KOBO_TOKEN = "d1eee51fe82dc1865d78a129d24bdd7489d35898"
ASSET_UID = "aZ8CQG5pB8FF2MpX4on7uV"
KOBO_BASE_URL = "https://kf.kobotoolbox.org"

HEADERS = {"Authorization": f"Token {KOBO_TOKEN}"}

# Nombres de los campos tal como los definiste en el formulario de Kobo.
# Ajustar si cambian en la versión publicada.
CAMPO_FOTO = "foto_cartel"
CAMPO_TIPO_FOTO = "tipo_foto"
CAMPO_DISTANCIA = "distancia_cartel"
CAMPO_TIPO_CARTEL = "tipo_cartel"
CAMPO_ESTADO_CARTEL = "estado_cartel"
CAMPO_OBSERVACIONES = "observaciones"
CAMPO_OPERADOR = "operador"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _descargar_foto(foto_url: str) -> bytes | None:
    """Descarga la foto desde Kobo (requiere autenticación con el token)."""
    try:
        r = requests.get(foto_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        logger.warning(f"No se pudo descargar la foto {foto_url}: {exc}")
        return None


def _obtener_url_foto(registro: dict, nombre_campo: str) -> str | None:
    """
    Busca la URL de descarga del adjunto correspondiente al campo foto
    dentro de _attachments, usando question_xpath para matchear el campo.
    Kobo devuelve solo el nombre del archivo en el campo, no la URL directa.
    """
    adjuntos = registro.get("_attachments", [])
    if not adjuntos:
        return None

    for adj in adjuntos:
        # Matchear por question_xpath (nombre del campo en el formulario)
        if adj.get("question_xpath") == nombre_campo and not adj.get("is_deleted"):
            # Usar download_url (tamaño original) con fallback a download_large_url
            return adj.get("download_url") or adj.get("download_large_url")

    return None


def _parsear_geopoint(registro: dict) -> tuple[float | None, float | None]:
    """Extrae lat/lon del campo _geolocation o del campo gps del formulario."""
    geo = registro.get("_geolocation")
    if geo and len(geo) == 2:
        try:
            return float(geo[0]), float(geo[1])
        except (TypeError, ValueError):
            pass
    # fallback: a veces viene como string "lat lon alt acc"
    gps_str = registro.get("ubicacion") or registro.get("gps") or ""
    partes = str(gps_str).split()
    if len(partes) >= 2:
        try:
            return float(partes[0]), float(partes[1])
        except ValueError:
            pass
    return None, None


def _procesar_foto(cartel: Cartel, contenido_foto: bytes) -> None:
    """
    Guarda la foto en el cartel, llama al detector y actualiza todos
    los campos calculados.
    """
    nombre_archivo = f"cartel_{cartel.kobo_id}.jpg"
    cartel.foto.save(nombre_archivo, ContentFile(contenido_foto), save=False)
    cartel.save(update_fields=["foto"])

    # Escribir la imagen en un archivo temporal para pasarla al detector
    ruta_foto = cartel.foto.path

    resultado = detectar_cartel(ruta_foto, cartel.distancia)

    error = resultado.get("error")

    if error == "imagen_ilegible":
        cartel.error_imagen_ilegible = True
        cartel.detalle_error = "La imagen descargada no pudo abrirse."
        cartel.estado_procesamiento = "error"

    elif error == "distancia_invalida":
        cartel.error_distancia_invalida = True
        cartel.detalle_error = "La distancia registrada en el formulario es nula, cero o negativa."
        cartel.estado_procesamiento = "error"

    elif error == "sin_deteccion":
        cartel.error_sin_deteccion = True
        cartel.cartel_detectado = False
        cartel.detalle_error = "YOLO no encontró ningún cartel en la imagen."
        cartel.estado_procesamiento = "error"

    else:
        cartel.cartel_detectado = resultado["detectado"]
        cartel.confianza_deteccion = resultado["confianza"]
        bbox = resultado["bbox"]
        if bbox:
            cartel.bbox_x = bbox["x"]
            cartel.bbox_y = bbox["y"]
            cartel.bbox_w = bbox["w"]
            cartel.bbox_h = bbox["h"]
        cartel.ancho_m = resultado["ancho_m"]
        cartel.alto_m = resultado["alto_m"]
        cartel.superficie_m2 = resultado["superficie_m2"]
        cartel.estado_procesamiento = "ok"

        # Marcar zoom sospechoso (no es error bloqueante, pero avisa al operador)
        if resultado.get("zoom_sospechoso"):
            cartel.error_zoom_sospechoso = True

        # OCR
        cartel.texto_ocr = resultado.get("texto_ocr")
        cartel.advertencia_sin_texto = resultado.get("sin_texto", False)

        # Guardar la imagen anotada con el bounding box
        foto_array = resultado.get("foto_anotada_array")
        if foto_array is not None:
            import cv2
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                cv2.imwrite(tmp.name, foto_array)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                cartel.foto_anotada.save(
                    f"anotada_{cartel.kobo_id}.jpg",
                    ContentFile(f.read()),
                    save=False,
                )
            os.unlink(tmp_path)

    cartel.save()


# ── Función principal ────────────────────────────────────────────────────────

def importar_kobo() -> dict:
    """
    Importa todos los registros nuevos desde KoboToolbox.

    Returns:
        dict con claves:
            - importados (int): registros nuevos procesados
            - omitidos (int): registros ya existentes
            - errores (int): registros con algún tipo de error
            - detalle (list[str]): mensajes de log
    """
    resumen = {"importados": 0, "omitidos": 0, "errores": 0, "detalle": []}

    url = f"{KOBO_BASE_URL}/api/v2/assets/{ASSET_UID}/data/?format=json"

    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        msg = f"Error al conectar con KoboToolbox: {exc}"
        logger.error(msg)
        resumen["detalle"].append(msg)
        return resumen

    registros = data.get("results", [])
    logger.info(f"Kobo devolvió {len(registros)} registros.")

    for registro in registros:
        kobo_id = str(registro.get("_id", ""))

        # Omitir duplicados (activos y descartados)
        existente = Cartel.objects.filter(kobo_id=kobo_id).first()
        if existente:
            if existente.estado_registro == "descartado":
                msg = f"[{kobo_id}] Descartado — se omite la reimportación."
                logger.info(msg)
            resumen["omitidos"] += 1
            continue

        # ── Extraer datos básicos del formulario ────────────────────────────
        lat, lon = _parsear_geopoint(registro)

        distancia_raw = registro.get(CAMPO_DISTANCIA)
        try:
            distancia = float(distancia_raw) if distancia_raw not in (None, "", "None") else None
        except (ValueError, TypeError):
            distancia = None

        fecha_str = registro.get("_submission_time")
        fecha = None
        if fecha_str:
            try:
                # Kobo envía fechas como "2026-03-15 18:58:50" (naive, UTC)
                # o como "2026-03-15T18:58:50Z". Normalizamos siempre a UTC aware.
                fecha_str_clean = fecha_str.replace("Z", "+00:00").replace(" ", "T")
                fecha = datetime.fromisoformat(fecha_str_clean)
                if fecha.tzinfo is None:
                    fecha = fecha.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # ── Crear el objeto Cartel ──────────────────────────────────────────
        cartel = Cartel(
            kobo_id=kobo_id,
            fecha=fecha,
            operador=registro.get(CAMPO_OPERADOR, ""),
            lat=lat,
            lon=lon,
            tipo_ubicacion_gps=registro.get("tipo_ubicacion_gps", ""),
            distancia=distancia,
            tipo_foto=registro.get(CAMPO_TIPO_FOTO, ""),
            tipo_cartel=registro.get(CAMPO_TIPO_CARTEL, ""),
            estado_cartel=registro.get(CAMPO_ESTADO_CARTEL, ""),
            observaciones=registro.get(CAMPO_OBSERVACIONES, ""),
            estado_procesamiento="pendiente",
        )

        # Validar GPS
        if lat is None or lon is None:
            cartel.error_gps_invalido = True
            cartel.estado_procesamiento = "error"

        # Validar distancia
        if distancia is None or distancia <= 0:
            cartel.error_distancia_invalida = True
            cartel.estado_procesamiento = "error"

        cartel.save()

        # ── Foto ────────────────────────────────────────────────────────────
        foto_url = _obtener_url_foto(registro, CAMPO_FOTO)

        if not foto_url:
            cartel.error_sin_foto = True
            cartel.detalle_error = "El formulario fue enviado sin foto adjunta."
            cartel.estado_procesamiento = "error"
            cartel.save(update_fields=["error_sin_foto", "detalle_error", "estado_procesamiento"])
            msg = f"[{kobo_id}] Sin foto — se registró el error para corrección."
            logger.warning(msg)
            resumen["detalle"].append(msg)
            resumen["errores"] += 1
            resumen["importados"] += 1
            continue

        contenido_foto = _descargar_foto(foto_url)

        if not contenido_foto:
            cartel.error_imagen_ilegible = True
            cartel.detalle_error = "No se pudo descargar la foto desde Kobo."
            cartel.estado_procesamiento = "error"
            cartel.save(update_fields=["error_imagen_ilegible", "detalle_error", "estado_procesamiento"])
            resumen["errores"] += 1
            resumen["importados"] += 1
            continue

        # ── Procesar foto con YOLO ──────────────────────────────────────────
        try:
            _procesar_foto(cartel, contenido_foto)
            if cartel.estado_procesamiento == "error":
                resumen["errores"] += 1
            msg = f"[{kobo_id}] OK — superficie: {cartel.superficie_m2} m²"
            logger.info(msg)
            resumen["detalle"].append(msg)
        except Exception as exc:
            cartel.detalle_error = str(exc)
            cartel.estado_procesamiento = "error"
            cartel.save(update_fields=["detalle_error", "estado_procesamiento"])
            msg = f"[{kobo_id}] Error inesperado: {exc}"
            logger.error(msg)
            resumen["detalle"].append(msg)
            resumen["errores"] += 1

        resumen["importados"] += 1

    logger.info(f"Importación finalizada: {resumen}")
    return resumen