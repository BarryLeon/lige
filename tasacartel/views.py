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
from django.db.models import Max, Q
import json


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _valores_tasa_json():
    """JSON con pk → {anio, valor_m2} para el front del formulario de liquidación."""
    return json.dumps({
        str(v.pk): {
            "valor_m2": str(v.valor_m2),
            "anio": v.anio,
        }
        for v in ValorTasaAnual.objects.all()
    })


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

            # Poblar snapshot de responsables desde el cartel,
            # pero respetar la superficie que el usuario ingresó en el form.
            superficie_ingresada = form.cleaned_data["superficie_m2"]
            liquidacion.poblar_desde_cartel()
            liquidacion.superficie_m2 = superficie_ingresada
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

    return render(request, "tasacartel/liquidacion_form.html", {
        "page_title":        "Nueva liquidación",
        "form":              form,
        "formset":           formset,
        "accion":            "Crear liquidación",
        "es_edicion":        False,
        "valores_tasa_json": _valores_tasa_json(),
    })


@login_required
def liquidacion_detalle(request, pk):
    liquidacion = get_object_or_404(
        Liquidacion.objects.select_related(
            "cartel", "propietario_cartel",
            "propietario_terreno", "empresa_publicista",
        ).prefetch_related("periodos__valor_tasa"),
        pk=pk,
    )

    # Raíz del árbol de versiones
    raiz = liquidacion.liquidacion_origen or liquidacion

    # Todas las versiones de esta familia, excluyendo la que se está viendo,
    # ordenadas de más nueva a más antigua.
    otras_versiones = (
        Liquidacion.objects
        .filter(Q(id=raiz.id) | Q(liquidacion_origen=raiz))
        .exclude(pk=liquidacion.pk)
        .order_by("-version")
    )

    plan = getattr(liquidacion, "plan_de_pago", None)

    return render(request, "tasacartel/liquidacion_detalle.html", {
        "page_title":      f"Liquidación #{liquidacion.id}",
        "liquidacion":     liquidacion,
        "otras_versiones": otras_versiones,
        "plan":            plan,
    })


@login_required
@transaction.atomic
def liquidacion_editar(request, pk):
    """
    Editar una liquidación siempre crea una nueva versión.
    Reglas:
    - Se determina la raíz del árbol de versiones.
    - La nueva versión tiene version = max(versiones del árbol) + 1.
    - La liquidación editada queda con estado "objetada".
    - Si la liquidación ya es un borrador, se permite editarla igualmente
      (se genera otra versión nueva y la anterior borrador queda objetada).
    - El botón "Editar" en el detalle siempre lleva a editar la versión
      más reciente del árbol, no necesariamente la que se está viendo.
    """
    original = get_object_or_404(Liquidacion, pk=pk)

    # Redirigir siempre a la última versión del árbol para evitar ramas paralelas.
    # Si el usuario llegó aquí desde una versión vieja, lo mandamos a la más nueva.
    raiz = original.liquidacion_origen or original
    ultima = (
        Liquidacion.objects
        .filter(Q(id=raiz.id) | Q(liquidacion_origen=raiz))
        .order_by("-version")
        .first()
    )
    if ultima and ultima.pk != original.pk:
        # El usuario está intentando editar una versión que ya no es la última.
        messages.warning(
            request,
            f"Estás editando la v{ultima.version} (la más reciente). "
            f"La v{original.version} que intentabas editar ya fue superada."
        )
        return redirect("tasacartel:liquidacion_editar", pk=ultima.pk)

    # A partir de aquí, `original` ES la última versión del árbol.

    if request.method == "POST":
        form    = LiquidacionForm(request.POST)
        # El formset se asocia a None porque la nueva liquidación aún no existe;
        # usamos prefix="periodos" para que coincida con el template.
        formset = LiquidacionPeriodoFormSet(request.POST, prefix="periodos")

        if form.is_valid() and formset.is_valid():

            # 1. Calcular nueva versión
            ultima_version = (
                Liquidacion.objects
                .filter(Q(id=raiz.id) | Q(liquidacion_origen=raiz))
                .aggregate(Max("version"))["version__max"] or 1
            )

            # 2. Crear nueva liquidación
            nueva = Liquidacion()
            nueva.cartel             = original.cartel
            nueva.version            = ultima_version + 1
            nueva.liquidacion_origen = raiz
            nueva.estado             = "borrador"
            nueva.generado_por       = request.user

            # Campos del form
            nueva.es_iluminado            = form.cleaned_data["es_iluminado"]
            nueva.aplica_descuento_ruta63 = form.cleaned_data["aplica_descuento_ruta63"]
            nueva.km_ruta63               = form.cleaned_data.get("km_ruta63")
            nueva.sobre_ruta2             = form.cleaned_data["sobre_ruta2"]
            nueva.observaciones           = form.cleaned_data.get("observaciones", "")

            # Snapshot de responsables (del cartel actual), luego sobreescribir superficie
            superficie_ingresada = form.cleaned_data["superficie_m2"]
            nueva.poblar_desde_cartel()
            nueva.superficie_m2 = superficie_ingresada

            nueva.save()

            # 3. Guardar períodos del formset (los que el usuario definió en el form)
            #    El formset no tiene instance porque es una liquidación nueva;
            #    seteamos liquidacion manualmente en cada período.
            periodos_a_guardar = formset.save(commit=False)
            for periodo in periodos_a_guardar:
                periodo.liquidacion       = nueva
                periodo.valor_m2_aplicado = periodo.valor_tasa.valor_m2
                periodo.calcular()
                periodo.save()

            # Períodos marcados para borrar en el formset (solo aplica si tienen pk)
            for obj in formset.deleted_objects:
                obj.delete()

            # Si el formset no tenía ningún período válido (el usuario no tocó nada),
            # clonar los períodos del original como fallback.
            if not nueva.periodos.exists():
                for periodo in original.periodos.all():
                    nuevo_periodo = LiquidacionPeriodo(
                        liquidacion       = nueva,
                        valor_tasa        = periodo.valor_tasa,
                        anio_fiscal       = periodo.anio_fiscal,
                        valor_m2_aplicado = periodo.valor_tasa.valor_m2,
                    )
                    nuevo_periodo.calcular()
                    nuevo_periodo.save()

            nueva.recalcular_totales()

            # 4. Marcar la versión anterior como objetada
            original.estado = "objetada"
            original.save(update_fields=["estado"])

            messages.success(
                request,
                f"Nueva versión v{nueva.version} generada. "
                f"La v{original.version} quedó marcada como objetada."
            )
            return redirect("tasacartel:liquidacion_detalle", pk=nueva.pk)

    else:
        # GET: pre-cargar el form con los datos del original
        form = LiquidacionForm(instance=original)
        # Pre-cargar el formset con los períodos existentes del original
        formset = LiquidacionPeriodoFormSet(
            instance=original,
            prefix="periodos",
        )

    return render(request, "tasacartel/liquidacion_form.html", {
        "page_title":        f"Editar Liquidación #{original.id} — nueva versión",
        "form":              form,
        "formset":           formset,
        "accion":            "Generar nueva versión",
        "es_edicion":        True,
        "original":          original,
        "valores_tasa_json": _valores_tasa_json(),
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


@login_required
def liquidacion_eliminar(request, pk):
    liq = get_object_or_404(Liquidacion, pk=pk)

    if liq.estado != "borrador":
        messages.error(request, "Solo se pueden eliminar borradores.")
        return redirect("tasacartel:liquidacion_detalle", pk=pk)

    if request.method == "POST":
        liq.delete()
        messages.success(request, "Liquidación eliminada.")
        return redirect("tasacartel:liquidaciones_lista")

    """# GET: pedir confirmación
    return render(request, "tasacartel/liquidacion_confirmar_eliminar.html", {
        "page_title": f"Eliminar Liquidación #{liq.id}",
        "liquidacion": liq,
    })
"""
    return render(request, "tasacartel/liquidacion_confirmar_eliminar.html", {
        "liquidacion": liq,
    })
# ════════════════════════════════════════════════════════════════════════════
# HISTORIAL POR CONTRIBUYENTE
# ════════════════════════════════════════════════════════════════════════════

@login_required
def historial_contribuyente(request, persona_id):
    from carteles.models import Persona
    persona = get_object_or_404(Persona, pk=persona_id)

    liquidaciones = Liquidacion.objects.filter(
        models_Q(propietario_cartel=persona)
        | models_Q(propietario_terreno=persona)
        | models_Q(empresa_publicista=persona)
    ).select_related(
        "cartel", "propietario_cartel",
        "propietario_terreno", "empresa_publicista",
    ).order_by("-fecha_determinacion", "-version")

    return render(request, "tasacartel/historial_contribuyente.html", {
        "page_title":   f"Historial — {persona.nombre_completo()}",
        "persona":      persona,
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
            "monto_anticipo":            round(liquidacion.monto_total * Decimal("0.10"), 2),
            "tasa_financiacion_mensual": Decimal("0.04"),
        })

    return render(request, "tasacartel/plan_form.html", {
        "page_title":  "Nuevo plan de pago",
        "form":        form,
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
        "plan":       plan,
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