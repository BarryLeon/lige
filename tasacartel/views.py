from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from decimal import Decimal
from django.db.models import Q as models_Q
from .models import ValorTasaAnual, Liquidacion, LiquidacionPeriodo, PlanDePago, CuotaPlan
from .forms import (
    ValorTasaAnualForm,
    LiquidacionForm,
    LiquidacionPeriodoFormSet,
    PlanDePagoForm,
)


# ════════════════════════════════════════════════════════════════════════════
# API INTERNA — datos del cartel para autocompletar el formulario
# ════════════════════════════════════════════════════════════════════════════

@login_required
def api_cartel_datos(request, cartel_id):
    """
    Devuelve JSON con los datos del cartel para pre-completar
    el formulario de liquidación: superficie_m2 y responsables.
    Solo carteles procesados OK y activos.
    """
    from carteles.models import Cartel
    try:
        cartel = Cartel.objects.select_related(
            "propietario_cartel",
            "parcela__propietario_terreno",
        ).get(
            pk=cartel_id,
            estado_procesamiento="ok",
            estado_registro="activo",
        )
    except Cartel.DoesNotExist:
        return JsonResponse({"error": "Cartel no encontrado"}, status=404)

    pub = cartel.publicidad_actual()

    data = {
        "superficie_m2": cartel.superficie_m2,
        "ancho_m":       cartel.ancho_m,
        "alto_m":        cartel.alto_m,
        "propietario_cartel": (
            cartel.propietario_cartel.nombre_completo()
            if cartel.propietario_cartel else "—"
        ),
        "propietario_terreno": (
            cartel.parcela.propietario_terreno.nombre_completo()
            if cartel.parcela and cartel.parcela.propietario_terreno else "—"
        ),
        "empresa_publicista": (
            pub.empresa.nombre_completo()
            if pub and pub.empresa else "Sin publicista registrado"
        ),
        "tipo_cartel": cartel.get_tipo_cartel_display() or "—",
        "ubicacion":   cartel.parcela.direccion if cartel.parcela else "—",
    }
    return JsonResponse(data)


# ════════════════════════════════════════════════════════════════════════════
# ABM VALORES DE TASA ANUAL
# ════════════════════════════════════════════════════════════════════════════

@login_required
def valores_tasa_lista(request):
    valores = ValorTasaAnual.objects.all().order_by("-anio")
    return render(request, "tasacartel/valores_tasa_lista.html", {
        "page_title": "Valores de tasa anual",
        "valores": valores,
    })


@login_required
def valor_tasa_crear(request):
    if request.method == "POST":
        form = ValorTasaAnualForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Valor de tasa guardado correctamente.")
            return redirect("tasacartel:valores_tasa_lista")
    else:
        form = ValorTasaAnualForm()
    return render(request, "tasacartel/valor_tasa_form.html", {
        "page_title": "Nuevo valor de tasa",
        "form": form,
        "accion": "Crear",
    })


@login_required
def valor_tasa_editar(request, pk):
    valor = get_object_or_404(ValorTasaAnual, pk=pk)
    if request.method == "POST":
        form = ValorTasaAnualForm(request.POST, instance=valor)
        if form.is_valid():
            form.save()
            messages.success(request, "Valor de tasa actualizado.")
            return redirect("tasacartel:valores_tasa_lista")
    else:
        form = ValorTasaAnualForm(instance=valor)
    return render(request, "tasacartel/valor_tasa_form.html", {
        "page_title": f"Editar tasa {valor.anio}",
        "form": form,
        "accion": "Guardar cambios",
        "valor": valor,
    })


@login_required
def valor_tasa_eliminar(request, pk):
    valor = get_object_or_404(ValorTasaAnual, pk=pk)
    if request.method == "POST":
        valor.delete()
        messages.success(request, f"Valor del año {valor.anio} eliminado.")
        return redirect("tasacartel:valores_tasa_lista")
    return render(request, "tasacartel/valor_tasa_confirmar_eliminar.html", {
        "page_title": f"Eliminar tasa {valor.anio}",
        "valor": valor,
    })


# ════════════════════════════════════════════════════════════════════════════
# LIQUIDACIONES
# ════════════════════════════════════════════════════════════════════════════

@login_required
def liquidaciones_lista(request):
    liquidaciones = (
        Liquidacion.objects
        .select_related(
            "cartel", "propietario_cartel",
            "propietario_terreno", "empresa_publicista",
        )
        .order_by("-fecha_determinacion", "-version")
    )
    return render(request, "tasacartel/liquidaciones_lista.html", {
        "page_title": "Liquidaciones",
        "liquidaciones": liquidaciones,
    })


@login_required
@transaction.atomic
def liquidacion_crear(request):
    if request.method == "POST":
        form    = LiquidacionForm(request.POST)
        formset = LiquidacionPeriodoFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            liquidacion = form.save(commit=False)
            liquidacion.generado_por = request.user

            # Poblar snapshot de responsables desde el cartel
            liquidacion.poblar_desde_cartel()

            # Si el usuario editó la superficie manualmente, respetarla
            liquidacion.superficie_m2 = form.cleaned_data["superficie_m2"]
            liquidacion.save()

            # Guardar períodos y calcular cada uno
            periodos = formset.save(commit=False)
            for periodo in periodos:
                periodo.liquidacion       = liquidacion
                periodo.valor_m2_aplicado = periodo.valor_tasa.valor_m2
                periodo.calcular()
                periodo.save()

            for obj in formset.deleted_objects:
                obj.delete()

            # Recalcular totales de la cabecera
            liquidacion.recalcular_totales()

            messages.success(request, f"Liquidación #{liquidacion.id} creada correctamente.")
            return redirect("tasacartel:liquidacion_detalle", pk=liquidacion.pk)
    else:
        form    = LiquidacionForm()
        formset = LiquidacionPeriodoFormSet()

    # Pasar valores de tasa como JSON para autocompletar en el front
    import json
    valores_tasa_json = json.dumps({
        str(v.pk): {
            "valor_m2": str(v.valor_m2),
            "anio": v.anio,
        }
        for v in ValorTasaAnual.objects.all()
    })

    return render(request, "tasacartel/liquidacion_form.html", {
        "page_title": "Nueva liquidación",
        "form": form,
        "formset": formset,
        "accion": "Crear liquidación",
        "valores_tasa_json": valores_tasa_json,
    })


@login_required
def liquidacion_detalle(request, pk):
    liquidacion = get_object_or_404(
        Liquidacion.objects.select_related(
            "cartel", "propietario_cartel",
            "propietario_terreno", "empresa_publicista",
            "generado_por",
        ).prefetch_related("periodos__valor_tasa"),
        pk=pk,
    )
    revisiones = liquidacion.revisiones.order_by("-version") if not liquidacion.liquidacion_origen else []
    plan = getattr(liquidacion, "plan_de_pago", None)

    return render(request, "tasacartel/liquidacion_detalle.html", {
        "page_title": f"Liquidación #{liquidacion.id}",
        "liquidacion": liquidacion,
        "revisiones": revisiones,
        "plan": plan,
    })


@login_required
@transaction.atomic
def liquidacion_editar(request, pk):
    """
    Editar una liquidación crea una nueva versión.
    La original queda en el historial como liquidacion_origen.
    """
    original = get_object_or_404(Liquidacion, pk=pk)

    if request.method == "POST":
        form    = LiquidacionForm(request.POST)
        formset = LiquidacionPeriodoFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            # Crear nueva versión
            nueva = form.save(commit=False)
            nueva.pk                 = None   # nueva instancia
            nueva.cartel             = original.cartel
            nueva.generado_por       = request.user
            nueva.version            = original.version + 1
            nueva.liquidacion_origen = original if not original.liquidacion_origen else original.liquidacion_origen
            nueva.estado             = "borrador"
            nueva.poblar_desde_cartel()
            nueva.superficie_m2 = form.cleaned_data["superficie_m2"]
            nueva.save()

            periodos = formset.save(commit=False)
            for periodo in periodos:
                periodo.pk          = None
                periodo.liquidacion = nueva
                periodo.valor_m2_aplicado = periodo.valor_tasa.valor_m2
                periodo.calcular()
                periodo.save()

            nueva.recalcular_totales()

            # Marcar original como objetada si venía de una objeción
            original.estado = "objetada"
            original.save(update_fields=["estado"])

            messages.success(request, f"Nueva versión v{nueva.version} generada.")
            return redirect("tasacartel:liquidacion_detalle", pk=nueva.pk)
    else:
        # Pre-cargar con datos de la liquidación original
        form    = LiquidacionForm(instance=original)
        formset = LiquidacionPeriodoFormSet(instance=original)

    import json
    valores_tasa_json = json.dumps({
        str(v.pk): {
            "valor_m2": str(v.valor_m2),
            "anio": v.anio,
        }
        for v in ValorTasaAnual.objects.all()
    })

    return render(request, "tasacartel/liquidacion_form.html", {
        "page_title": f"Editar liquidación #{original.id} → nueva versión",
        "form": form,
        "formset": formset,
        "accion": "Generar nueva versión",
        "es_edicion": True,
        "original": original,
        "valores_tasa_json": valores_tasa_json,
    })


@login_required
def liquidacion_cambiar_estado(request, pk):
    """Cambio rápido de estado vía POST (botones en el detalle)."""
    liquidacion = get_object_or_404(Liquidacion, pk=pk)
    if request.method == "POST":
        nuevo_estado = request.POST.get("estado")
        estados_validos = [e[0] for e in Liquidacion.ESTADO_CHOICES]
        if nuevo_estado in estados_validos:
            liquidacion.estado = nuevo_estado
            liquidacion.save(update_fields=["estado"])
            messages.success(request, f"Estado actualizado a: {liquidacion.get_estado_display()}")
    return redirect("tasacartel:liquidacion_detalle", pk=pk)


# ════════════════════════════════════════════════════════════════════════════
# HISTORIAL POR CONTRIBUYENTE
# ════════════════════════════════════════════════════════════════════════════

@login_required
def historial_contribuyente(request, persona_id):
    from carteles.models import Persona
    persona = get_object_or_404(Persona, pk=persona_id)

    # Liquidaciones donde figura como cualquiera de los tres responsables
    liquidaciones = Liquidacion.objects.filter(
        models_Q(propietario_cartel=persona)
        | models_Q(propietario_terreno=persona)
        | models_Q(empresa_publicista=persona)
    ).select_related(
        "cartel", "propietario_cartel",
        "propietario_terreno", "empresa_publicista",
    ).order_by("-fecha_determinacion", "-version")

    return render(request, "tasacartel/historial_contribuyente.html", {
        "page_title": f"Historial — {persona.nombre_completo()}",
        "persona": persona,
        "liquidaciones": liquidaciones,
    })


# ════════════════════════════════════════════════════════════════════════════
# PLAN DE PAGO
# ════════════════════════════════════════════════════════════════════════════

@login_required
@transaction.atomic
def plan_crear(request, liquidacion_pk):
    liquidacion = get_object_or_404(
        Liquidacion, pk=liquidacion_pk, estado="conformada"
    )

    if hasattr(liquidacion, "plan_de_pago"):
        messages.warning(request, "Esta liquidación ya tiene un plan de pago.")
        return redirect("tasacartel:liquidacion_detalle", pk=liquidacion_pk)

    if request.method == "POST":
        form = PlanDePagoForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.liquidacion      = liquidacion
            plan.monto_deuda_base = liquidacion.monto_total
            plan.save()
            plan.generar_cuotas()

            liquidacion.estado = "en_plan"
            liquidacion.save(update_fields=["estado"])

            messages.success(request, f"Plan de pago #{plan.id} creado con {plan.cantidad_cuotas + 1} cuotas.")
            return redirect("tasacartel:plan_detalle", pk=plan.pk)
    else:
        form = PlanDePagoForm(initial={
            "monto_anticipo": round(liquidacion.monto_total * Decimal("0.10"), 2),
            "tasa_financiacion_mensual": Decimal("0.04"),
        })

    return render(request, "tasacartel/plan_form.html", {
        "page_title": "Nuevo plan de pago",
        "form": form,
        "liquidacion": liquidacion,
    })


@login_required
def plan_detalle(request, pk):
    plan = get_object_or_404(
        PlanDePago.objects.select_related(
            "liquidacion__cartel",
            "liquidacion__propietario_cartel",
        ).prefetch_related("cuotas"),
        pk=pk,
    )
    total_plan = sum(c.monto_total for c in plan.cuotas.all()) + plan.monto_anticipo

    return render(request, "tasacartel/plan_detalle.html", {
        "page_title": f"Plan de pago #{plan.id}",
        "plan": plan,
        "total_plan": total_plan,
    })


@login_required
def cuota_marcar_pagada(request, cuota_pk):
    """Marca una cuota como pagada vía POST."""
    cuota = get_object_or_404(CuotaPlan, pk=cuota_pk)
    if request.method == "POST":
        from django.utils import timezone
        cuota.pagado     = True
        cuota.fecha_pago = timezone.now().date()
        cuota.save(update_fields=["pagado", "fecha_pago"])
        messages.success(request, f"Cuota {cuota.nro_cuota} marcada como pagada.")
    return redirect("tasacartel:plan_detalle", pk=cuota.plan_id)


# Fix: importar Q correctamente
