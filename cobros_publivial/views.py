from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date

from tasacartel.models import Liquidacion as LiquidacionCartel, CuotaPlan
from tasas_marchiquita.models import Liquidacion as LiquidacionMarchiquita
from .models import HonorarioLiquidacion, HonorarioCuotaItem, Factura, FacturaItem
from .services import (
    generar_factura_marchiquita,
    generar_honorario_tasacartel_contado,
    generar_honorario_tasacartel_cuotas,
    generar_factura_tasacartel,
    marcar_factura_cobrada,
    anular_factura,
    honorarios_sin_factura,
    liquidaciones_marchiquita_sin_factura,
    cuotas_sin_honorario,
    liquidaciones_contado_sin_honorario,
    resumen_facturacion,
)


@login_required(login_url='login')
def dashboard_view(request):
    resumen_total       = resumen_facturacion()
    resumen_cartel      = resumen_facturacion(app_origen='tasacartel')
    resumen_marchiquita = resumen_facturacion(app_origen='tasas_marchiquita')
    facturas_recientes  = Factura.objects.order_by('-fecha_emision')[:10]

    return render(request, 'cobros_publivial/dashboard.html', {
        'resumen_total'       : resumen_total,
        'resumen_cartel'      : resumen_cartel,
        'resumen_marchiquita' : resumen_marchiquita,
        'facturas_recientes'  : facturas_recientes,
    })


@login_required(login_url='login')
def facturas_lista_view(request):
    app    = request.GET.get('app', '')
    estado = request.GET.get('estado', '')
    facturas = Factura.objects.prefetch_related('items__honorario').order_by('-fecha_emision')
    if app:
        facturas = facturas.filter(app_origen=app)
    if estado:
        facturas = facturas.filter(estado=estado)
    return render(request, 'cobros_publivial/facturas_lista.html', {
        'facturas'      : facturas,
        'filtro_app'    : app,
        'filtro_estado' : estado,
        'estados'       : Factura.Estado.choices,
    })


@login_required(login_url='login')
def factura_detalle_view(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    items   = factura.items.select_related('honorario').all()
    return render(request, 'cobros_publivial/factura_detalle.html', {
        'factura': factura,
        'items'  : items,
    })


@login_required(login_url='login')
def factura_cobrada_view(request, factura_id):
    if request.method != 'POST':
        return redirect('cobros_publivial:facturas_lista')
    try:
        factura = marcar_factura_cobrada(factura_id, usuario=request.user)
        messages.success(request, f"Factura {factura.numero} marcada como cobrada.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('cobros_publivial:factura_detalle', factura_id=factura_id)


@login_required(login_url='login')
def factura_anular_view(request, factura_id):
    if request.method != 'POST':
        return redirect('cobros_publivial:facturas_lista')
    observacion = request.POST.get('observacion', '')
    try:
        factura = anular_factura(factura_id, observacion=observacion, usuario=request.user)
        messages.success(request, f"Factura {factura.numero} anulada.")
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('cobros_publivial:factura_detalle', factura_id=factura_id)


@login_required(login_url='login')
def cartel_honorarios_view(request):
    """Lista honorarios de tasacartel sin facturar. Permite agruparlos en una factura."""
    honorarios = honorarios_sin_factura(app_origen='tasacartel').order_by('-periodo')
    today = timezone.now().date()

    if request.method == 'POST':
        ids = request.POST.getlist('honorarios_ids')
        if not ids:
            messages.error(request, "Seleccioná al menos un honorario para facturar.")
            return redirect('cobros_publivial:cartel_honorarios')
        numero_factura = request.POST.get('numero_factura', '')
        fecha_emision = parse_date(request.POST.get('fecha_emision', ''))
        try:
            honorarios_sel = HonorarioLiquidacion.objects.filter(pk__in=ids)
            factura = generar_factura_tasacartel(
                honorarios_sel,
                numero_factura=numero_factura,
                fecha_emision=fecha_emision,
                usuario=request.user,
            )
            messages.success(
                request,
                f"Se registró la factura {factura.numero} por $ {factura.monto_total:,.2f}."
            )
            return redirect('cobros_publivial:factura_detalle', factura_id=factura.pk)
        except ValueError as e:
            messages.error(request, str(e))

    return render(request, 'cobros_publivial/cartel_honorarios.html', {
        'honorarios': honorarios,
        'today'     : today,
    })


@login_required(login_url='login')
def cartel_contado_view(request):
    """Liquidaciones de tasacartel pagadas de contado sin honorario generado."""
    liquidaciones = liquidaciones_contado_sin_honorario()

    if request.method == 'POST':
        liquidacion_id = request.POST.get('liquidacion_id')
        porcentaje = request.POST.get('porcentaje', '')
        try:
            liq = LiquidacionCartel.objects.using('carteles').get(pk=liquidacion_id)
            honorario = generar_honorario_tasacartel_contado(
                liq,
                porcentaje=porcentaje,
                usuario=request.user,
            )
            messages.success(
                request,
                f"Honorario generado por $ {honorario.monto_honorario:,.2f}. "
                "Podés incluirlo en una factura desde el panel de honorarios."
            )
        except LiquidacionCartel.DoesNotExist:
            messages.error(request, f"No existe la liquidación #{liquidacion_id}.")
        except ValueError as e:
            messages.error(request, str(e))
        return redirect('cobros_publivial:cartel_contado')

    return render(request, 'cobros_publivial/cartel_contado.html', {
        'liquidaciones': liquidaciones,
    })


@login_required(login_url='login')
def cartel_cuotas_view(request):
    """CuotaPlan de tasacartel pagadas que aún no tienen honorario."""
    cuotas = cuotas_sin_honorario()
    today  = timezone.now().date()

    if request.method == 'POST':
        ids = request.POST.getlist('cuotas_ids')
        if not ids:
            messages.error(request, "Seleccioná al menos una cuota.")
            return redirect('cobros_publivial:cartel_cuotas')
        porcentaje = request.POST.get('porcentaje', '')
        try:
            periodo_str = request.POST.get('periodo')
            anio, mes = periodo_str.split('-')
            periodo = date(int(anio), int(mes), 1)
        except Exception:
            periodo = today.replace(day=1)
        try:
            cuotas_sel = list(
                CuotaPlan.objects.using('carteles').filter(pk__in=ids)
                .select_related('plan__liquidacion')
            )
            honorario = generar_honorario_tasacartel_cuotas(
                cuotas_sel,
                periodo,
                porcentaje=porcentaje,
                usuario=request.user,
            )
            messages.success(
                request,
                f"Honorario generado por $ {honorario.monto_honorario:,.2f} "
                f"({len(cuotas_sel)} cuota{'s' if len(cuotas_sel) != 1 else ''}). "
                "Podés incluirlo en una factura desde el panel de honorarios."
            )
            return redirect('cobros_publivial:cartel_honorarios')
        except ValueError as e:
            messages.error(request, str(e))

    return render(request, 'cobros_publivial/cartel_cuotas.html', {
        'cuotas': cuotas,
        'today' : today,
    })


@login_required(login_url='login')
def marchiquita_honorarios_view(request):
    liquidaciones = liquidaciones_marchiquita_sin_factura()
    facturas = (
        Factura.objects
        .filter(app_origen='tasas_marchiquita')
        .order_by('-fecha_emision')
    )
    today = timezone.now().date()

    if request.method == 'POST':
        liquidacion_id = request.POST.get('liquidacion_id')
        numero_factura = request.POST.get('numero_factura', '')
        fecha_emision = parse_date(request.POST.get('fecha_emision', ''))
        try:
            liquidacion = LiquidacionMarchiquita.objects.get(pk=liquidacion_id)
            honorario, factura = generar_factura_marchiquita(
                liquidacion,
                numero_factura=numero_factura,
                fecha_emision=fecha_emision,
                usuario=request.user,
            )
            messages.success(
                request,
                f"Se registró la factura {factura.numero} por "
                f"$ {factura.monto_total:,.2f} sobre la liquidación #{liquidacion.pk}."
            )
            return redirect('cobros_publivial:factura_detalle', factura_id=factura.pk)
        except LiquidacionMarchiquita.DoesNotExist:
            messages.error(request, f"No existe la liquidación #{liquidacion_id}.")
        except ValueError as e:
            messages.error(request, str(e))
        return redirect('cobros_publivial:marchiquita_honorarios')

    return render(request, 'cobros_publivial/marchiquita_honorarios.html', {
        'liquidaciones': liquidaciones,
        'facturas': facturas[:10],
        'today': today,
    })
