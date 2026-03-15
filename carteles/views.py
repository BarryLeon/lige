from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Cartel
from .servicios.importar_kobo import importar_kobo
from .servicios.kobo_delete import borrar_submission_kobo


# ── Lista de carteles ────────────────────────────────────────────────────────

def lista_carteles(request):
    """
    Muestra todos los carteles con su estado de procesamiento.
    Permite filtrar por estado.
    """
    filtro = request.GET.get("estado", "")
    mostrar_descartados = request.GET.get("descartados") == "1"

    # Por defecto solo mostrar activos
    if mostrar_descartados:
        carteles = Cartel.objects.filter(estado_registro="descartado")
    else:
        carteles = Cartel.objects.filter(estado_registro="activo")

    if filtro in ("pendiente", "ok", "error"):
        carteles = carteles.filter(estado_procesamiento=filtro)

    base_activos = Cartel.objects.filter(estado_registro="activo")
    totales = {
        "total":       base_activos.count(),
        "ok":          base_activos.filter(estado_procesamiento="ok").count(),
        "error":       base_activos.filter(estado_procesamiento="error").count(),
        "pendiente":   base_activos.filter(estado_procesamiento="pendiente").count(),
        "descartados": Cartel.objects.filter(estado_registro="descartado").count(),
    }

    return render(
        request,
        "carteles/lista.html",
        {
            "carteles": carteles,
            "totales": totales,
            "filtro_activo": filtro,
            "mostrar_descartados": mostrar_descartados,
        },
    )


# ── Detalle de un cartel ─────────────────────────────────────────────────────

def detalle_cartel(request, pk):
    """Muestra el detalle completo de un cartel: foto, mapa, superficie y errores."""
    cartel = get_object_or_404(Cartel, pk=pk)
    return render(request, "carteles/detalle.html", {"cartel": cartel})


# ── Importar desde Kobo ──────────────────────────────────────────────────────

def importar_desde_kobo(request):
    """
    Vista que dispara la importación desde KoboToolbox.
    Solo acepta POST para evitar importaciones accidentales.
    """
    if request.method != "POST":
        return redirect("carteles_lista")

    try:
        resumen = importar_kobo()
        messages.success(
            request,
            f"Importación completada: {resumen['importados']} nuevos registros, "
            f"{resumen['omitidos']} ya existían, {resumen['errores']} con errores.",
        )
    except Exception as exc:
        messages.error(request, f"Error durante la importación: {exc}")

    return redirect("carteles_lista")


# ── Reprocesar un cartel ─────────────────────────────────────────────────────

def reprocesar_cartel(request, pk):
    """
    Vuelve a correr el detector sobre la foto de un cartel ya importado.
    Útil cuando se corrigió un error de distancia o se subió una foto nueva.
    """
    if request.method != "POST":
        return redirect("carteles_lista")

    cartel = get_object_or_404(Cartel, pk=pk)

    if not cartel.foto:
        messages.error(request, "Este cartel no tiene foto. No se puede reprocesar.")
        return redirect("carteles_detalle", pk=pk)

    from .servicios.cartel_detector import detectar_cartel

    # Limpiar errores previos de detección
    cartel.error_sin_deteccion = False
    cartel.error_imagen_ilegible = False
    cartel.error_distancia_invalida = False
    cartel.detalle_error = None
    cartel.estado_procesamiento = "pendiente"
    cartel.save()

    resultado = detectar_cartel(cartel.foto.path, cartel.distancia)
    error = resultado.get("error")

    if error == "imagen_ilegible":
        cartel.error_imagen_ilegible = True
        cartel.detalle_error = "La imagen no pudo abrirse."
        cartel.estado_procesamiento = "error"
        messages.error(request, "La imagen está corrupta o no pudo leerse.")

    elif error == "distancia_invalida":
        cartel.error_distancia_invalida = True
        cartel.detalle_error = "Distancia inválida."
        cartel.estado_procesamiento = "error"
        messages.error(request, "La distancia registrada es inválida. Editá el registro antes de reprocesar.")

    elif error == "sin_deteccion":
        cartel.error_sin_deteccion = True
        cartel.cartel_detectado = False
        cartel.detalle_error = "YOLO no encontró ningún cartel."
        cartel.estado_procesamiento = "error"
        messages.warning(request, "No se detectó ningún cartel en la imagen.")

    else:
        cartel.cartel_detectado = resultado["detectado"]
        cartel.confianza_deteccion = resultado["confianza"]
        bbox = resultado["bbox"]
        if bbox:
            cartel.bbox_x, cartel.bbox_y = bbox["x"], bbox["y"]
            cartel.bbox_w, cartel.bbox_h = bbox["w"], bbox["h"]
        cartel.ancho_m = resultado["ancho_m"]
        cartel.alto_m = resultado["alto_m"]
        cartel.superficie_m2 = resultado["superficie_m2"]
        cartel.estado_procesamiento = "ok"
        if resultado.get("zoom_sospechoso"):
            cartel.error_zoom_sospechoso = True

        # OCR
        cartel.texto_ocr = resultado.get("texto_ocr")
        cartel.advertencia_sin_texto = resultado.get("sin_texto", False)

        # Guardar foto anotada con el bbox dibujado
        foto_array = resultado.get("foto_anotada_array")
        if foto_array is not None:
            import cv2, tempfile, os
            from django.core.files.base import ContentFile
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                cv2.imwrite(tmp.name, foto_array)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                nombre = f"anotada_{cartel.kobo_id or cartel.id}.jpg"
                cartel.foto_anotada.save(nombre, ContentFile(f.read()), save=False)
            os.unlink(tmp_path)

        messages.success(
            request,
            f"Reprocesado OK — {cartel.ancho_m}m × {cartel.alto_m}m = {cartel.superficie_m2} m²"
            f" [{resultado.get('metodo_deteccion')}]"
        )

    cartel.save()
    return redirect("carteles_detalle", pk=pk)


# ── Descartar un cartel ──────────────────────────────────────────────────────

def descartar_cartel(request, pk):
    """
    Marca el cartel como descartado con un motivo obligatorio.
    Opcionalmente borra la submission de KoboToolbox.
    """
    if request.method != "POST":
        return redirect("carteles_detalle", pk=pk)

    cartel = get_object_or_404(Cartel, pk=pk)
    motivo = request.POST.get("motivo", "").strip()
    borrar_en_kobo = request.POST.get("borrar_en_kobo") == "1"

    if not motivo:
        messages.error(request, "Debés ingresar un motivo para descartar el registro.")
        return redirect("carteles_detalle", pk=pk)

    cartel.estado_registro  = "descartado"
    cartel.motivo_descarte  = motivo
    cartel.descartado_por   = request.user
    cartel.descartado_en    = timezone.now()
    cartel.save()

    messages.warning(request, f"Cartel #{cartel.id} marcado como descartado.")

    # Borrar en Kobo si el usuario lo pidió
    if borrar_en_kobo and cartel.kobo_id:
        resultado = borrar_submission_kobo(cartel.kobo_id)
        if resultado["ok"]:
            messages.success(request, "Formulario borrado de KoboToolbox correctamente.")
        else:
            messages.error(
                request,
                f"No se pudo borrar de KoboToolbox: {resultado['detalle']}. "
                "Borralo manualmente desde la plataforma."
            )

    return redirect("carteles_lista")


# ── Restaurar un cartel descartado ───────────────────────────────────────────

def restaurar_cartel(request, pk):
    """Vuelve a marcar un cartel descartado como activo."""
    if request.method != "POST":
        return redirect("carteles_detalle", pk=pk)

    cartel = get_object_or_404(Cartel, pk=pk)
    cartel.estado_registro = "activo"
    cartel.motivo_descarte = None
    cartel.descartado_por  = None
    cartel.descartado_en   = None
    cartel.save()

    messages.success(request, f"Cartel #{cartel.id} restaurado como activo.")
    return redirect("carteles_detalle", pk=pk)