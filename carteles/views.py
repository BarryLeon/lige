from django.contrib import messages
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from django.conf import settings
from django.http import HttpResponse

from .models import Cartel, HistorialPublicidad, Parcela, Persona
from .servicios.exportar import exportar_excel, exportar_pdf
from .servicios.importar_kobo import importar_kobo
from .servicios.kobo_delete import borrar_submission_kobo


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

    cartel = get_object_or_404(Cartel, pk=pk)

    if not cartel.foto:
        messages.error(request, "Este cartel no tiene foto. No se puede reprocesar.")
        return redirect("carteles_detalle", pk=pk)

    from .servicios.cartel_detector import detectar_cartel

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
        messages.error(request, "La distancia registrada es inválida.")

    elif error == "sin_deteccion":
        cartel.error_sin_deteccion = True
        cartel.cartel_detectado = False
        cartel.detalle_error = "No se encontró ningún cartel."
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
        cartel.texto_ocr = resultado.get("texto_ocr")
        cartel.advertencia_sin_texto = resultado.get("sin_texto", False)

        foto_array = resultado.get("foto_anotada_array")
        if foto_array is not None:
            import cv2, tempfile, os
            from django.core.files.base import ContentFile
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                cv2.imwrite(tmp.name, foto_array)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                cartel.foto_anotada.save(
                    f"anotada_{cartel.kobo_id or cartel.id}.jpg",
                    ContentFile(f.read()), save=False,
                )
            os.unlink(tmp_path)

        messages.success(
            request,
            f"Reprocesado OK — {cartel.ancho_m}m × {cartel.alto_m}m = "
            f"{cartel.superficie_m2} m² [{resultado.get('metodo_deteccion')}]"
        )

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
    empresa     = request.POST.get("empresa", "").strip()
    fecha_desde = request.POST.get("fecha_desde") or None
    fecha_hasta = request.POST.get("fecha_hasta") or None

    if not empresa:
        messages.error(request, "El nombre de la empresa es obligatorio.")
        return redirect("carteles_detalle", pk=pk)

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
    messages.success(request, f"Publicidad de '{empresa}' agregada.")
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
    qs = Cartel.objects.filter(estado_registro="activo").select_related(
        "parcela", "propietario_cartel", "parcela__propietario_terreno"
    ).prefetch_related("historial_publicidad")

    # ── Búsqueda ─────────────────────────────────────────────────────────────
    q           = request.GET.get("q", "").strip()
    fecha_desde = request.GET.get("fecha_desde", "")
    fecha_hasta = request.GET.get("fecha_hasta", "")
    tipo_cartel = request.GET.get("tipo_cartel", "")
    estado_proc = request.GET.get("estado_proc", "")

    if q:
        qs = qs.filter(
            Q(texto_ocr__icontains=q)         |
            Q(observaciones__icontains=q)      |
            Q(operador__icontains=q)           |
            Q(kobo_id__icontains=q)            |
            Q(propietario_cartel__apellido__icontains=q)    |
            Q(propietario_cartel__razon_social__icontains=q)|
            Q(parcela__direccion__icontains=q) |
            Q(historial_publicidad__empresa__icontains=q)
        ).distinct()

    if fecha_desde:
        qs = qs.filter(fecha__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(fecha__date__lte=fecha_hasta)
    if tipo_cartel:
        qs = qs.filter(tipo_cartel=tipo_cartel)
    if estado_proc:
        qs = qs.filter(estado_procesamiento=estado_proc)

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
        "q":          q,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "tipo_cartel": tipo_cartel,
        "estado_proc": estado_proc,
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
    qs = Cartel.objects.filter(estado_registro="activo").select_related(
        "parcela", "propietario_cartel", "parcela__propietario_terreno"
    ).prefetch_related("historial_publicidad")

    q           = request.GET.get("q", "").strip()
    fecha_desde = request.GET.get("fecha_desde", "")
    fecha_hasta = request.GET.get("fecha_hasta", "")
    tipo_cartel = request.GET.get("tipo_cartel", "")
    estado_proc = request.GET.get("estado_proc", "")

    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(texto_ocr__icontains=q)          |
            Q(observaciones__icontains=q)       |
            Q(operador__icontains=q)            |
            Q(kobo_id__icontains=q)             |
            Q(propietario_cartel__apellido__icontains=q)     |
            Q(propietario_cartel__razon_social__icontains=q) |
            Q(parcela__direccion__icontains=q)  |
            Q(historial_publicidad__empresa__icontains=q)
        ).distinct()
    if fecha_desde:
        qs = qs.filter(fecha__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(fecha__date__lte=fecha_hasta)
    if tipo_cartel:
        qs = qs.filter(tipo_cartel=tipo_cartel)
    if estado_proc:
        qs = qs.filter(estado_procesamiento=estado_proc)
    return qs


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
    qs = _get_queryset_filtrado(request)
    from datetime import datetime
    nombre = f"carteles_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    contenido = exportar_pdf(qs, settings.MEDIA_ROOT)
    response = HttpResponse(contenido, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return response