import json
import os
import tempfile
from urllib.parse import urlencode

from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from django.conf import settings
from django.http import HttpResponse

from .models import Cartel, HistorialPublicidad, Parcela, Persona
from .servicios.exportar import exportar_excel, exportar_pdf, exportar_pdf_detallado
from .servicios.importar_kobo import importar_kobo
from .servicios.kobo_delete import borrar_submission_kobo


CARTELES_DB_ALIAS = "carteles"


def _resetear_estado_deteccion(cartel):
    cartel.cartel_detectado = None
    cartel.confianza_deteccion = None
    cartel.bbox_x = None
    cartel.bbox_y = None
    cartel.bbox_w = None
    cartel.bbox_h = None
    cartel.ancho_m = None
    cartel.alto_m = None
    cartel.superficie_m2 = None
    cartel.texto_ocr = None
    cartel.advertencia_sin_texto = False
    cartel.error_sin_deteccion = False
    cartel.error_imagen_ilegible = False
    cartel.error_distancia_invalida = False
    cartel.error_zoom_sospechoso = False
    cartel.metodo_superficie = None
    cartel.origen_medicion = None
    cartel.detalle_error = None
    cartel.diagnostico_geometria_inconsistente = False
    cartel.detalle_diagnostico = None
    cartel.estado_procesamiento = "pendiente"


def _parsear_esquinas_manuales(payload):
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        raise ValidationError("No se pudieron interpretar las esquinas enviadas.")

    if not isinstance(data, list) or len(data) != 4:
        raise ValidationError("Debés marcar exactamente 4 esquinas.")

    esquinas = []
    for punto in data:
        if not isinstance(punto, dict):
            raise ValidationError("Formato inválido de esquinas.")
        try:
            x = float(punto["x"])
            y = float(punto["y"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError("Cada esquina debe tener coordenadas válidas.")
        esquinas.append({"x": round(x, 2), "y": round(y, 2)})
    return esquinas


def _guardar_foto_anotada(cartel, foto_array):
    if foto_array is None:
        return

    import cv2

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        cv2.imwrite(tmp.name, foto_array)
        tmp_path = tmp.name
    with open(tmp_path, "rb") as f:
        cartel.foto_anotada.save(
            f"anotada_{cartel.kobo_id or cartel.id}.jpg",
            ContentFile(f.read()),
            save=False,
        )
    os.unlink(tmp_path)


def _aplicar_resultado_detector(cartel, resultado):
    error = resultado.get("error")

    if error == "imagen_ilegible":
        cartel.error_imagen_ilegible = True
        cartel.detalle_error = "La imagen no pudo abrirse."
        cartel.estado_procesamiento = "error"
        return "imagen_ilegible"

    if error == "distancia_invalida":
        cartel.error_distancia_invalida = True
        cartel.detalle_error = "Distancia inválida."
        cartel.estado_procesamiento = "error"
        return "distancia_invalida"

    if error == "sin_deteccion":
        cartel.error_sin_deteccion = True
        cartel.cartel_detectado = False
        cartel.detalle_error = "No se encontró ningún cartel."
        cartel.estado_procesamiento = "error"
        return "sin_deteccion"

    cartel.cartel_detectado = resultado["detectado"]
    cartel.confianza_deteccion = resultado["confianza"]
    bbox = resultado["bbox"]
    if bbox:
        cartel.bbox_x, cartel.bbox_y = bbox["x"], bbox["y"]
        cartel.bbox_w, cartel.bbox_h = bbox["w"], bbox["h"]
    cartel.ancho_m = resultado["ancho_m"]
    cartel.alto_m = resultado["alto_m"]
    cartel.superficie_m2 = resultado["superficie_m2"]
    cartel.metodo_superficie = resultado.get("metodo_superficie")
    cartel.origen_medicion = resultado.get("origen_medicion")
    cartel.diagnostico_geometria_inconsistente = resultado.get(
        "diagnostico_geometria_inconsistente",
        False,
    )
    cartel.detalle_diagnostico = resultado.get("detalle_diagnostico")
    cartel.estado_procesamiento = "ok"
    if resultado.get("zoom_sospechoso"):
        cartel.error_zoom_sospechoso = True
    cartel.texto_ocr = resultado.get("texto_ocr")
    cartel.advertencia_sin_texto = resultado.get("sin_texto", False)

    _guardar_foto_anotada(cartel, resultado.get("foto_anotada_array"))
    return None


def _reprocesar_cartel(cartel):
    from .servicios.cartel_detector import detectar_cartel

    _resetear_estado_deteccion(cartel)
    cartel.save()
    return detectar_cartel(
        cartel.foto.path,
        cartel.distancia,
        esquinas_manuales=cartel.manual_esquinas,
    )


def _obtener_cartel_para_actualizar(pk):
    return get_object_or_404(
        Cartel.objects.using(CARTELES_DB_ALIAS).select_for_update(),
        pk=pk,
    )


def _get_carteles_base_queryset():
    return Cartel.objects.filter(estado_registro="activo").select_related(
        "parcela", "propietario_cartel", "parcela__propietario_terreno"
    ).prefetch_related("historial_publicidad", "historial_publicidad__empresa")


def _obtener_filtros_informes(data):
    return {
        "propietario": data.get("propietario", "").strip(),
        "parcela": data.get("parcela", "").strip(),
        "texto_ocr": data.get("texto_ocr", "").strip(),
        "fecha_desde": data.get("fecha_desde", ""),
        "fecha_hasta": data.get("fecha_hasta", ""),
        "tipo_cartel": data.get("tipo_cartel", ""),
        "estado_proc": data.get("estado_proc", ""),
    }


def _aplicar_filtros_informes(qs, filtros):
    propietario = filtros["propietario"]
    parcela = filtros["parcela"]
    texto_ocr = filtros["texto_ocr"]
    fecha_desde = filtros["fecha_desde"]
    fecha_hasta = filtros["fecha_hasta"]
    tipo_cartel = filtros["tipo_cartel"]
    estado_proc = filtros["estado_proc"]

    if propietario:
        personas_ids = Persona.objects.filter(
            Q(apellido__icontains=propietario)
            | Q(nombre__icontains=propietario)
            | Q(razon_social__icontains=propietario)
            | Q(cuit_dni__icontains=propietario)
        ).values("id")

        qs = qs.filter(
            Q(propietario_cartel_id__in=personas_ids)
            | Q(parcela__propietario_terreno_id__in=personas_ids)
        )

    if parcela:
        qs = qs.filter(
            Q(parcela__circunscripcion__icontains=parcela)
            | Q(parcela__seccion__icontains=parcela)
            | Q(parcela__chacra__icontains=parcela)
            | Q(parcela__parcela_nro__icontains=parcela)
            | Q(parcela__direccion__icontains=parcela)
            | Q(parcela__localidad__icontains=parcela)
        )

    if texto_ocr:
        qs = qs.filter(Q(texto_ocr__icontains=texto_ocr) | Q(observaciones__icontains=texto_ocr))

    if fecha_desde:
        qs = qs.filter(fecha__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(fecha__date__lte=fecha_hasta)
    if tipo_cartel:
        qs = qs.filter(tipo_cartel=tipo_cartel)
    if estado_proc:
        qs = qs.filter(estado_procesamiento=estado_proc)

    return qs.distinct()


def _build_querystring_filtros(filtros):
    return urlencode({clave: valor for clave, valor in filtros.items() if valor})


def _redirect_informes_con_filtros(request, filtros, nivel, mensaje):
    getattr(messages, nivel)(request, mensaje)
    url = reverse("carteles_informes")
    query = _build_querystring_filtros(filtros)
    if query:
        url = f"{url}?{query}"
    return redirect(url)


def _resolver_queryset_exportacion(request):
    filtros = _obtener_filtros_informes(request.POST if request.method == "POST" else request.GET)
    qs = _aplicar_filtros_informes(_get_carteles_base_queryset(), filtros)

    if request.method != "POST":
        return qs, filtros, None

    alcance = request.POST.get("alcance_pdf", "filtrados")
    cartel_ids = request.POST.getlist("cartel_ids")
    if alcance == "seleccionados":
        if not cartel_ids:
            return None, filtros, _redirect_informes_con_filtros(
                request,
                filtros,
                "warning",
                "Seleccioná al menos un cartel para generar el PDF de la selección.",
            )
        qs = qs.filter(id__in=cartel_ids)

    return qs.distinct(), filtros, None


# ── Lista de carteles ────────────────────────────────────────────────────────

def lista_carteles(request):
    filtro = request.GET.get("estado", "")
    mostrar_descartados = request.GET.get("descartados") == "1"

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

    return render(request, "carteles/lista.html", {
        "carteles": carteles,
        "totales": totales,
        "filtro_activo": filtro,
        "mostrar_descartados": mostrar_descartados,
    })


# ── Detalle de un cartel ─────────────────────────────────────────────────────

def detalle_cartel(request, pk):
    cartel = get_object_or_404(Cartel, pk=pk)
    personas = Persona.objects.all().order_by("apellido", "razon_social")
    parcelas = Parcela.objects.all().order_by("circunscripcion", "parcela_nro")
    return render(request, "carteles/detalle.html", {
        "cartel": cartel,
        "personas": personas,
        "parcelas": parcelas,
        "historial": cartel.historial_publicidad.all(),
    })


# ── Importar desde Kobo ──────────────────────────────────────────────────────

def importar_desde_kobo(request):
    if request.method != "POST":
        return redirect("carteles_lista")
    try:
        resumen = importar_kobo()
        messages.success(
            request,
            f"Importación completada: {resumen['importados']} nuevos, "
            f"{resumen['omitidos']} ya existían, {resumen['errores']} con errores.",
        )
    except Exception as exc:
        messages.error(request, f"Error durante la importación: {exc}")
    return redirect("carteles_lista")


# ── Reprocesar un cartel ─────────────────────────────────────────────────────

def reprocesar_cartel(request, pk):
    if request.method != "POST":
        return redirect("carteles_lista")

    with transaction.atomic(using=CARTELES_DB_ALIAS):
        cartel = _obtener_cartel_para_actualizar(pk)

        if not cartel.foto:
            messages.error(request, "Este cartel no tiene foto. No se puede reprocesar.")
            return redirect("carteles_detalle", pk=pk)

        resultado = _reprocesar_cartel(cartel)
        error = _aplicar_resultado_detector(cartel, resultado)

        if error == "imagen_ilegible":
            messages.error(request, "La imagen está corrupta o no pudo leerse.")
        elif error == "distancia_invalida":
            messages.error(request, "La distancia registrada es inválida.")
        elif error == "sin_deteccion":
            messages.warning(request, "No se detectó ningún cartel en la imagen.")
        else:
            messages.success(
                request,
                f"Reprocesado OK — {cartel.ancho_m}m × {cartel.alto_m}m = "
                f"{cartel.superficie_m2} m² "
                f"[{resultado.get('metodo_deteccion')}/{resultado.get('metodo_superficie')}]"
            )

        cartel.save()
    return redirect("carteles_detalle", pk=pk)


def corregir_distancia_y_reprocesar(request, pk):
    if request.method != "POST":
        return redirect("carteles_lista")

    distancia_str = (request.POST.get("distancia") or "").strip().replace(",", ".")
    try:
        nueva_distancia = float(distancia_str)
    except ValueError:
        messages.error(request, "Ingresá una distancia válida en metros.")
        return redirect("carteles_detalle", pk=pk)

    with transaction.atomic(using=CARTELES_DB_ALIAS):
        cartel = _obtener_cartel_para_actualizar(pk)

        if not cartel.foto:
            messages.error(request, "Este cartel no tiene foto. No se puede recalcular.")
            return redirect("carteles_detalle", pk=pk)

        cartel.distancia = nueva_distancia
        try:
            cartel.full_clean()
        except ValidationError as exc:
            messages.error(request, f"No se pudo guardar la nueva distancia: {exc}")
            return redirect("carteles_detalle", pk=pk)

        cartel.save(update_fields=["distancia", "actualizado"])

        resultado = _reprocesar_cartel(cartel)
        error = _aplicar_resultado_detector(cartel, resultado)

        if error == "imagen_ilegible":
            messages.error(request, "La imagen está corrupta o no pudo leerse.")
        elif error == "distancia_invalida":
            messages.error(request, "La nueva distancia registrada es inválida.")
        elif error == "sin_deteccion":
            messages.warning(request, "No se detectó ningún cartel en la imagen.")
        else:
            messages.success(
                request,
                f"Distancia actualizada a {cartel.distancia:.1f} m y superficie recalculada: "
                f"{cartel.superficie_m2} m² [{cartel.origen_medicion}/{resultado.get('metodo_superficie')}]"
            )

        cartel.save()
    return redirect("carteles_detalle", pk=pk)


def guardar_esquinas_manuales(request, pk):
    if request.method != "POST":
        return redirect("carteles_lista")

    try:
        esquinas = _parsear_esquinas_manuales(request.POST.get("esquinas_json"))
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("carteles_detalle", pk=pk)

    with transaction.atomic(using=CARTELES_DB_ALIAS):
        cartel = _obtener_cartel_para_actualizar(pk)
        if not cartel.foto:
            messages.error(request, "Este cartel no tiene foto. No se pueden guardar esquinas.")
            return redirect("carteles_detalle", pk=pk)

        cartel.manual_esquinas = esquinas
        cartel.save(update_fields=["manual_esquinas", "actualizado"])

        resultado = _reprocesar_cartel(cartel)
        error = _aplicar_resultado_detector(cartel, resultado)

        if error:
            messages.warning(
                request,
                "Se guardaron las esquinas manuales, pero no se pudo recalcular correctamente.",
            )
        else:
            messages.success(
                request,
                f"Esquinas manuales guardadas. Nueva superficie: {cartel.superficie_m2} m² "
                f"[{cartel.origen_medicion}/{cartel.metodo_superficie}]"
            )

        cartel.save()
    return redirect("carteles_detalle", pk=pk)


def quitar_esquinas_manuales(request, pk):
    if request.method != "POST":
        return redirect("carteles_lista")

    with transaction.atomic(using=CARTELES_DB_ALIAS):
        cartel = _obtener_cartel_para_actualizar(pk)
        cartel.manual_esquinas = None
        cartel.save(update_fields=["manual_esquinas", "actualizado"])

        if not cartel.foto:
            messages.success(request, "Se volvió al modo automático.")
            return redirect("carteles_detalle", pk=pk)

        resultado = _reprocesar_cartel(cartel)
        error = _aplicar_resultado_detector(cartel, resultado)

        if error:
            messages.warning(
                request,
                "Se quitaron las esquinas manuales, pero el reproceso automático tuvo problemas.",
            )
        else:
            messages.success(request, "Se volvió al modo automático de detección.")

        cartel.save()
    return redirect("carteles_detalle", pk=pk)


# ── Descartar / Restaurar ────────────────────────────────────────────────────

def descartar_cartel(request, pk):
    if request.method != "POST":
        return redirect("carteles_detalle", pk=pk)

    cartel = get_object_or_404(Cartel, pk=pk)
    motivo = request.POST.get("motivo", "").strip()
    borrar_en_kobo = request.POST.get("borrar_en_kobo") == "1"

    if not motivo:
        messages.error(request, "Debés ingresar un motivo para descartar el registro.")
        return redirect("carteles_detalle", pk=pk)

    cartel.estado_registro = "descartado"
    cartel.motivo_descarte = motivo
    cartel.descartado_por  = request.user
    cartel.descartado_en   = timezone.now()
    cartel.save()
    messages.warning(request, f"Cartel #{cartel.id} marcado como descartado.")

    if borrar_en_kobo and cartel.kobo_id:
        resultado = borrar_submission_kobo(cartel.kobo_id)
        if resultado["ok"]:
            messages.success(request, "Formulario borrado de KoboToolbox.")
        else:
            messages.error(request, f"No se pudo borrar de Kobo: {resultado['detalle']}")

    return redirect("carteles_lista")


def restaurar_cartel(request, pk):
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


# ── Actualizar datos de gestión del cartel (parcela, propietario) ────────────

def actualizar_gestion_cartel(request, pk):
    """Actualiza parcela y propietario del cartel desde el detalle."""
    if request.method != "POST":
        return redirect("carteles_detalle", pk=pk)

    cartel = get_object_or_404(Cartel, pk=pk)

    parcela_id = request.POST.get("parcela_id")
    propietario_id = request.POST.get("propietario_id")

    cartel.parcela = Parcela.objects.filter(pk=parcela_id).first() if parcela_id else None
    cartel.propietario_cartel = Persona.objects.filter(pk=propietario_id).first() if propietario_id else None
    cartel.save()
    messages.success(request, "Datos de gestión actualizados.")
    return redirect("carteles_detalle", pk=pk)


# ── Publicidad del cartel ────────────────────────────────────────────────────

def agregar_publicidad(request, pk):
    """Agrega una nueva empresa al historial de publicidad del cartel."""
    if request.method != "POST":
        return redirect("carteles_detalle", pk=pk)

    cartel = get_object_or_404(Cartel, pk=pk)
    empresa_id  = request.POST.get("empresa_id", "").strip()
    fecha_desde = request.POST.get("fecha_desde") or None
    fecha_hasta = request.POST.get("fecha_hasta") or None

    if not empresa_id:
        messages.error(request, "Debés seleccionar una empresa.")
        return redirect("carteles_detalle", pk=pk)

    empresa = get_object_or_404(Persona, pk=empresa_id)

    # Si no tiene fecha_hasta es la actual: cerrar la publicidad anterior
    if not fecha_hasta:
        cartel.historial_publicidad.filter(fecha_hasta__isnull=True).update(
            fecha_hasta=fecha_desde or timezone.now().date()
        )

    HistorialPublicidad.objects.create(
        cartel=cartel,
        empresa=empresa,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
    )
    messages.success(request, f"Publicidad de '{empresa.nombre_completo()}' agregada.")
    return redirect("carteles_detalle", pk=pk)


def eliminar_publicidad(request, pk, pub_id):
    """Elimina un registro del historial de publicidad."""
    if request.method != "POST":
        return redirect("carteles_detalle", pk=pk)

    pub = get_object_or_404(HistorialPublicidad, pk=pub_id, cartel_id=pk)
    pub.delete()
    messages.success(request, "Registro de publicidad eliminado.")
    return redirect("carteles_detalle", pk=pk)


# ════════════════════════════════════════════════════════════════════════════
# INFORMES Y ESTADÍSTICAS
# ════════════════════════════════════════════════════════════════════════════

def informes(request):
    """
    Vista principal de informes con búsqueda y estadísticas.
    """
    filtros = _obtener_filtros_informes(request.GET)
    qs = _aplicar_filtros_informes(_get_carteles_base_queryset(), filtros)

    # ── Estadísticas generales ───────────────────────────────────────────────
    base = Cartel.objects.filter(estado_registro="activo", estado_procesamiento="ok")

    stats = {
        "total_carteles":    Cartel.objects.filter(estado_registro="activo").count(),
        "superficie_total":  base.aggregate(t=Sum("superficie_m2"))["t"] or 0,
        "sin_propietario":   Cartel.objects.filter(estado_registro="activo", propietario_cartel__isnull=True).count(),
        "sin_parcela":       Cartel.objects.filter(estado_registro="activo", parcela__isnull=True).count(),
        "por_tipo": list(
            Cartel.objects.filter(estado_registro="activo")
            .values("tipo_cartel")
            .annotate(cantidad=Count("id"), superficie=Sum("superficie_m2"))
            .order_by("-cantidad")
        ),
        "por_estado_fisico": list(
            Cartel.objects.filter(estado_registro="activo")
            .values("estado_cartel")
            .annotate(cantidad=Count("id"))
            .order_by("-cantidad")
        ),
    }

    return render(request, "carteles/informes.html", {
        "carteles":   qs,
        "stats":      stats,
        **filtros,
        "tipo_choices": Cartel.TIPO_CARTEL_CHOICES,
    })


# ════════════════════════════════════════════════════════════════════════════
# ABM PERSONAS
# ════════════════════════════════════════════════════════════════════════════

def lista_personas(request):
    q = request.GET.get("q", "").strip()
    personas = Persona.objects.all()
    if q:
        personas = personas.filter(
            Q(apellido__icontains=q) | Q(nombre__icontains=q) |
            Q(razon_social__icontains=q) | Q(cuit_dni__icontains=q)
        )
    return render(request, "carteles/personas/lista.html", {
        "personas": personas, "q": q,
    })


def crear_persona(request):
    if request.method == "POST":
        try:
            p = Persona(
                tipo         = request.POST.get("tipo", "fisica"),
                apellido     = request.POST.get("apellido", "").strip() or None,
                nombre       = request.POST.get("nombre", "").strip() or None,
                razon_social = request.POST.get("razon_social", "").strip() or None,
                cuit_dni     = request.POST.get("cuit_dni", "").strip(),
                domicilio    = request.POST.get("domicilio", "").strip() or None,
                telefono     = request.POST.get("telefono", "").strip() or None,
                email        = request.POST.get("email", "").strip() or None,
                observaciones = request.POST.get("observaciones", "").strip() or None,
            )
            p.full_clean()
            p.save()
            messages.success(request, f"Persona '{p.nombre_completo()}' creada correctamente.")
            return redirect("carteles_personas_lista")
        except Exception as e:
            messages.error(request, f"Error al guardar: {e}")

    return render(request, "carteles/personas/form.html", {"accion": "Crear"})


def editar_persona(request, pk):
    persona = get_object_or_404(Persona, pk=pk)
    if request.method == "POST":
        try:
            persona.tipo          = request.POST.get("tipo", "fisica")
            persona.apellido      = request.POST.get("apellido", "").strip() or None
            persona.nombre        = request.POST.get("nombre", "").strip() or None
            persona.razon_social  = request.POST.get("razon_social", "").strip() or None
            persona.cuit_dni      = request.POST.get("cuit_dni", "").strip()
            persona.domicilio     = request.POST.get("domicilio", "").strip() or None
            persona.telefono      = request.POST.get("telefono", "").strip() or None
            persona.email         = request.POST.get("email", "").strip() or None
            persona.observaciones = request.POST.get("observaciones", "").strip() or None
            persona.full_clean()
            persona.save()
            messages.success(request, "Persona actualizada.")
            return redirect("carteles_personas_lista")
        except Exception as e:
            messages.error(request, f"Error al guardar: {e}")

    return render(request, "carteles/personas/form.html", {
        "accion": "Editar", "persona": persona,
    })


# ════════════════════════════════════════════════════════════════════════════
# ABM PARCELAS
# ════════════════════════════════════════════════════════════════════════════

def lista_parcelas(request):
    q = request.GET.get("q", "").strip()
    parcelas = Parcela.objects.select_related("propietario_terreno").all()
    if q:
        parcelas = parcelas.filter(
            Q(circunscripcion__icontains=q) | Q(seccion__icontains=q) |
            Q(parcela_nro__icontains=q)     | Q(direccion__icontains=q) |
            Q(propietario_terreno__apellido__icontains=q) |
            Q(propietario_terreno__razon_social__icontains=q)
        )
    return render(request, "carteles/parcelas/lista.html", {
        "parcelas": parcelas, "q": q,
    })


def crear_parcela(request):
    personas = Persona.objects.all().order_by("apellido", "razon_social")
    if request.method == "POST":
        try:
            prop_id = request.POST.get("propietario_terreno_id")
            p = Parcela(
                circunscripcion     = request.POST.get("circunscripcion", "").strip() or None,
                seccion             = request.POST.get("seccion", "").strip() or None,
                chacra              = request.POST.get("chacra", "").strip() or None,
                parcela_nro         = request.POST.get("parcela_nro", "").strip() or None,
                direccion           = request.POST.get("direccion", "").strip() or None,
                localidad           = request.POST.get("localidad", "").strip() or None,
                observaciones       = request.POST.get("observaciones", "").strip() or None,
                propietario_terreno = Persona.objects.filter(pk=prop_id).first() if prop_id else None,
            )
            p.save()
            messages.success(request, f"Parcela '{p.nomenclatura()}' creada.")
            return redirect("carteles_parcelas_lista")
        except Exception as e:
            messages.error(request, f"Error al guardar: {e}")

    return render(request, "carteles/parcelas/form.html", {
        "accion": "Crear", "personas": personas,
    })


def editar_parcela(request, pk):
    parcela  = get_object_or_404(Parcela, pk=pk)
    personas = Persona.objects.all().order_by("apellido", "razon_social")
    if request.method == "POST":
        try:
            prop_id = request.POST.get("propietario_terreno_id")
            parcela.circunscripcion     = request.POST.get("circunscripcion", "").strip() or None
            parcela.seccion             = request.POST.get("seccion", "").strip() or None
            parcela.chacra              = request.POST.get("chacra", "").strip() or None
            parcela.parcela_nro         = request.POST.get("parcela_nro", "").strip() or None
            parcela.direccion           = request.POST.get("direccion", "").strip() or None
            parcela.localidad           = request.POST.get("localidad", "").strip() or None
            parcela.observaciones       = request.POST.get("observaciones", "").strip() or None
            parcela.propietario_terreno = Persona.objects.filter(pk=prop_id).first() if prop_id else None
            parcela.save()
            messages.success(request, "Parcela actualizada.")
            return redirect("carteles_parcelas_lista")
        except Exception as e:
            messages.error(request, f"Error al guardar: {e}")

    return render(request, "carteles/parcelas/form.html", {
        "accion": "Editar", "parcela": parcela, "personas": personas,
    })


# ════════════════════════════════════════════════════════════════════════════
# EXPORTACIONES
# ════════════════════════════════════════════════════════════════════════════

def _get_queryset_filtrado(request):
    """Reutiliza los mismos filtros que la vista de informes."""
    filtros = _obtener_filtros_informes(request.GET)
    return _aplicar_filtros_informes(_get_carteles_base_queryset(), filtros)


def exportar_excel_view(request):
    qs = _get_queryset_filtrado(request)
    from datetime import datetime
    nombre = f"carteles_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    contenido = exportar_excel(qs)
    response = HttpResponse(
        contenido,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return response


def exportar_pdf_view(request):
    qs, _, redirect_response = _resolver_queryset_exportacion(request)
    if redirect_response:
        return redirect_response
    from datetime import datetime
    nombre = f"carteles_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    contenido = exportar_pdf(qs, settings.MEDIA_ROOT)
    response = HttpResponse(contenido, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return response


def exportar_pdf_detalle_view(request):
    qs, _, redirect_response = _resolver_queryset_exportacion(request)
    if redirect_response:
        return redirect_response

    from datetime import datetime

    nombre = f"carteles_detalle_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    contenido = exportar_pdf_detallado(qs, settings.MEDIA_ROOT)
    response = HttpResponse(contenido, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return response
