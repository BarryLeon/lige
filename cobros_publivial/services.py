"""
cobros_publivial/services.py

Servicios de negocio para la app cobros_publivial.

Flujos:
  - tasas_marchiquita : la liquidación ya trae la comisión a facturar; en cobros
                        se carga la factura y luego se marca el cobro
  - tasacartel contado: honorario manual desde dashboard (liquidacion.estado == 'pagada'
                        sin plan de pago). Base = liquidacion.monto_total
  - tasacartel cuotas : honorario manual seleccionando CuotaPlan con pagado=True
                        que aún no tienen honorario. Base = suma de monto_total de cuotas.
"""

from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.db import transaction

from .models import HonorarioLiquidacion, HonorarioCuotaItem, Factura, FacturaItem


# ──────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────

def _calcular_monto_honorario(base: Decimal, porcentaje: Decimal) -> Decimal:
    return (base * porcentaje / Decimal('100')).quantize(Decimal('0.01'))


def _normalizar_porcentaje(porcentaje) -> Decimal:
    """
    Convierte el porcentaje ingresado a Decimal y valida que sea mayor a cero.
    """
    if porcentaje in (None, ''):
        raise ValueError("Debés informar el porcentaje del honorario.")
    try:
        valor = Decimal(str(porcentaje).replace(',', '.')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise ValueError("El porcentaje del honorario es inválido.")
    if valor <= 0:
        raise ValueError("El porcentaje del honorario debe ser mayor a cero.")
    return valor


# ──────────────────────────────────────────────────────────────────
# 1. TASAS MARCHIQUITA
#    La liquidación ya contiene la comisión/honorario a facturar.
#    En cobros se registra la factura cuando efectivamente se presenta.
# ──────────────────────────────────────────────────────────────────

@transaction.atomic
def generar_factura_marchiquita(
    liquidacion_marchiquita,
    numero_factura: str,
    fecha_emision,
    usuario=None,
):
    """
    Registra la factura de una tasas_marchiquita.Liquidacion ya generada.
    Si todavía no existe el HonorarioLiquidacion snapshot, lo crea.

    Parámetros:
        liquidacion_marchiquita : instancia de tasas_marchiquita.Liquidacion
        numero_factura          : número real de factura a registrar
        fecha_emision           : fecha de emisión de la factura
        usuario                 : instancia de auth.User (opcional)

    Retorna:
        (HonorarioLiquidacion, Factura)

    Lanza:
        ValueError si ya existe una factura para esa liquidación,
        o si faltan datos de factura.
    """
    numero_factura = (numero_factura or '').strip()
    if not numero_factura:
        raise ValueError("Debés informar el número de factura.")
    if not fecha_emision:
        raise ValueError("Debés informar la fecha de emisión de la factura.")

    honorario_existente = HonorarioLiquidacion.objects.filter(
        liquidacion_marchiquita_id=liquidacion_marchiquita.pk
    ).first()
    if honorario_existente and hasattr(honorario_existente, 'factura_item'):
        raise ValueError(
            f"La liquidación de Marchiquita #{liquidacion_marchiquita.pk} "
            "ya tiene una factura registrada."
        )

    if Factura.objects.filter(numero=numero_factura).exists():
        raise ValueError(f"Ya existe una factura con número {numero_factura}.")

    periodo = liquidacion_marchiquita.importacion.periodo
    monto = liquidacion_marchiquita.comision
    if not monto or monto <= 0:
        raise ValueError(
            f"La liquidación #{liquidacion_marchiquita.pk} no tiene comisión válida."
        )

    if honorario_existente:
        honorario = honorario_existente
    else:
        honorario = HonorarioLiquidacion.objects.create(
            app_origen                 = HonorarioLiquidacion.AppOrigen.TASAS_MARCHIQUITA,
            tipo_origen                = HonorarioLiquidacion.TipoOrigen.MARCHIQUITA,
            liquidacion_marchiquita_id = liquidacion_marchiquita.pk,
            periodo                    = periodo,
            base_calculo               = monto,
            porcentaje_aplicado        = Decimal('100.00'),
            monto_honorario            = monto,
            generado_por               = usuario,
        )

    factura = Factura.objects.create(
        numero        = numero_factura,
        app_origen    = 'tasas_marchiquita',
        fecha_emision = fecha_emision,
        monto_total   = monto,
        estado        = Factura.Estado.PRESENTADA,
        creado_por    = usuario,
    )
    FacturaItem.objects.create(
        factura   = factura,
        honorario = honorario,
        monto     = monto,
    )

    return honorario, factura


def _generar_numero_factura(prefijo: str, periodo) -> str:
    """
    Genera un número de factura único: PREFIJO-YYYYMM-NNN
    Ejemplo: MARCH-202403-001, CARTEL-202403-005
    """
    base = f"{prefijo.upper()}-{periodo.strftime('%Y%m')}-"
    ultimo = (
        Factura.objects
        .filter(numero__startswith=base)
        .order_by('-numero')
        .values_list('numero', flat=True)
        .first()
    )
    if ultimo:
        try:
            secuencia = int(ultimo.split('-')[-1]) + 1
        except ValueError:
            secuencia = 1
    else:
        secuencia = 1
    return f"{base}{secuencia:03d}"


# ──────────────────────────────────────────────────────────────────
# 2. TASACARTEL — CONTADO
#    Liquidaciones con estado='pagada' que NO tienen plan de pago.
#    Base de cálculo: liquidacion.monto_total
# ──────────────────────────────────────────────────────────────────

@transaction.atomic
def generar_honorario_tasacartel_contado(
    liquidacion_tasacartel,
    porcentaje,
    usuario=None,
):
    """
    Genera un HonorarioLiquidacion para una liquidación de tasacartel
    pagada de contado (estado='pagada', sin PlanDePago asociado).

    Parámetros:
        liquidacion_tasacartel : instancia de tasacartel.Liquidacion
        usuario                : instancia de auth.User (opcional)

    Retorna:
        HonorarioLiquidacion

    Lanza:
        ValueError si ya existe honorario de contado para esa liquidación,
        si no está en estado pagada, si tiene plan de pago,
        o si el porcentaje ingresado es inválido.
    """
    if liquidacion_tasacartel.estado != 'pagada':
        raise ValueError(
            f"La liquidación #{liquidacion_tasacartel.pk} no está en estado 'pagada' "
            f"(estado actual: {liquidacion_tasacartel.estado})."
        )

    # Verificar que no tenga plan de pago (eso sería cuotas, no contado)
    if hasattr(liquidacion_tasacartel, 'plan_de_pago'):
        raise ValueError(
            f"La liquidación #{liquidacion_tasacartel.pk} tiene plan de pago. "
            "Usá el flujo de cuotas para generar su honorario."
        )

    if HonorarioLiquidacion.objects.filter(
        liquidacion_tasacartel_id=liquidacion_tasacartel.pk,
        tipo_origen=HonorarioLiquidacion.TipoOrigen.CONTADO,
    ).exists():
        raise ValueError(
            f"Ya existe un honorario de contado para la liquidación "
            f"#{liquidacion_tasacartel.pk}."
        )

    base   = liquidacion_tasacartel.monto_total
    if not base or base <= 0:
        raise ValueError(
            f"La liquidación #{liquidacion_tasacartel.pk} tiene monto total en cero."
        )

    periodo = liquidacion_tasacartel.fecha_determinacion.replace(day=1)
    porcentaje = _normalizar_porcentaje(porcentaje)
    monto   = _calcular_monto_honorario(Decimal(str(base)), porcentaje)

    honorario = HonorarioLiquidacion.objects.create(
        app_origen                = HonorarioLiquidacion.AppOrigen.TASACARTEL,
        tipo_origen               = HonorarioLiquidacion.TipoOrigen.CONTADO,
        liquidacion_tasacartel_id = liquidacion_tasacartel.pk,
        periodo                   = periodo,
        base_calculo              = Decimal(str(base)),
        porcentaje_aplicado       = porcentaje,
        monto_honorario           = monto,
        generado_por              = usuario,
    )

    return honorario


# ──────────────────────────────────────────────────────────────────
# 3. TASACARTEL — CUOTAS
#    CuotaPlan con pagado=True que aún no tienen HonorarioCuotaItem.
#    Base: suma de cuota.monto_total de las cuotas seleccionadas.
# ──────────────────────────────────────────────────────────────────

@transaction.atomic
def generar_honorario_tasacartel_cuotas(cuotas, periodo, porcentaje, usuario=None):
    """
    Genera un HonorarioLiquidacion agrupando varias CuotaPlan pagadas
    de tasacartel que aún no tienen honorario.

    Parámetros:
        cuotas  : lista de tasacartel.CuotaPlan con pagado=True
        periodo : date — primer día del mes de facturación
        usuario : instancia de auth.User (opcional)

    Retorna:
        HonorarioLiquidacion

    Lanza:
        ValueError si alguna cuota ya tiene honorario,
        si la lista está vacía, si alguna no está pagada,
        o si el porcentaje ingresado es inválido.
    """
    if not cuotas:
        raise ValueError("Debés seleccionar al menos una cuota para generar el honorario.")

    cuotas_ids = [c.pk for c in cuotas]

    # Verificar que todas estén pagadas
    no_pagadas = [c.pk for c in cuotas if not c.pagado]
    if no_pagadas:
        raise ValueError(
            f"Las siguientes cuotas no están marcadas como pagadas: {no_pagadas}."
        )

    # Verificar que ninguna tenga honorario ya generado
    ids_ya_usados = list(
        HonorarioCuotaItem.objects
        .filter(cuota_id__in=cuotas_ids)
        .values_list('cuota_id', flat=True)
    )
    if ids_ya_usados:
        raise ValueError(
            f"Las siguientes cuotas ya tienen honorario generado: {ids_ya_usados}. "
            "Quitálas de la selección."
        )

    porcentaje = _normalizar_porcentaje(porcentaje)

    # Base = suma de monto_total de cuotas seleccionadas (snapshot)
    base  = sum(Decimal(str(c.monto_total)) for c in cuotas)
    monto = _calcular_monto_honorario(base, porcentaje)

    # Referencia a la liquidación de la primera cuota
    liquidacion_id = cuotas[0].plan.liquidacion_id

    honorario = HonorarioLiquidacion.objects.create(
        app_origen                = HonorarioLiquidacion.AppOrigen.TASACARTEL,
        tipo_origen               = HonorarioLiquidacion.TipoOrigen.CUOTAS,
        liquidacion_tasacartel_id = liquidacion_id,
        periodo                   = periodo,
        base_calculo              = base,
        porcentaje_aplicado       = porcentaje,
        monto_honorario           = monto,
        generado_por              = usuario,
    )

    HonorarioCuotaItem.objects.bulk_create([
        HonorarioCuotaItem(
            honorario   = honorario,
            cuota_id    = c.pk,
            monto_cuota = Decimal(str(c.monto_total)),
        )
        for c in cuotas
    ])

    return honorario


# ──────────────────────────────────────────────────────────────────
# 4. FACTURA TASACARTEL
#    Agrupa varios HonorarioLiquidacion de tasacartel en una factura.
# ──────────────────────────────────────────────────────────────────

@transaction.atomic
def generar_factura_tasacartel(honorarios, numero_factura, fecha_emision, usuario=None):
    """
    Crea una Factura agrupando varios honorarios de tasacartel
    que aún no tienen factura asociada.

    Parámetros:
        honorarios : queryset o lista de HonorarioLiquidacion sin factura
        numero_factura : número real de factura
        fecha_emision  : fecha de emisión
        usuario    : instancia de auth.User (opcional)

    Retorna:
        Factura

    Lanza:
        ValueError si la lista está vacía,
        si algún honorario ya tiene factura,
        o si hay honorarios de app distinta a tasacartel.
    """
    if not honorarios:
        raise ValueError("Debés seleccionar al menos un honorario para generar la factura.")
    numero_factura = (numero_factura or '').strip()
    if not numero_factura:
        raise ValueError("Debés informar el número de factura.")
    if not fecha_emision:
        raise ValueError("Debés informar la fecha de emisión de la factura.")
    if Factura.objects.filter(numero=numero_factura).exists():
        raise ValueError(f"Ya existe una factura con número {numero_factura}.")

    honorarios_list = list(honorarios)

    no_cartel = [h for h in honorarios_list if h.app_origen != HonorarioLiquidacion.AppOrigen.TASACARTEL]
    if no_cartel:
        raise ValueError(
            "Todos los honorarios deben ser de tasacartel. "
            f"Encontrados de otra app: {[h.pk for h in no_cartel]}"
        )

    ids = [h.pk for h in honorarios_list]
    ya_facturados = list(
        FacturaItem.objects
        .filter(honorario_id__in=ids)
        .values_list('honorario_id', flat=True)
    )
    if ya_facturados:
        raise ValueError(
            f"Los siguientes honorarios ya están facturados: {ya_facturados}. "
            "Quitálos de la selección."
        )

    total  = sum(h.monto_honorario for h in honorarios_list)

    factura = Factura.objects.create(
        numero        = numero_factura,
        app_origen    = 'tasacartel',
        fecha_emision = fecha_emision,
        monto_total   = total,
        estado        = Factura.Estado.PRESENTADA,
        creado_por    = usuario,
    )

    FacturaItem.objects.bulk_create([
        FacturaItem(
            factura   = factura,
            honorario = h,
            monto     = h.monto_honorario,
        )
        for h in honorarios_list
    ])

    return factura


# ──────────────────────────────────────────────────────────────────
# 5. CAMBIAR ESTADO DE FACTURA
# ──────────────────────────────────────────────────────────────────

@transaction.atomic
def marcar_factura_cobrada(factura_id, fecha_cobro=None, usuario=None):
    """
    Marca una factura como cobrada.

    Lanza ValueError si no existe, ya está cobrada, o está anulada.
    """
    try:
        factura = Factura.objects.get(pk=factura_id)
    except Factura.DoesNotExist:
        raise ValueError(f"No existe la factura #{factura_id}.")

    if factura.estado == Factura.Estado.COBRADA:
        raise ValueError(f"La factura {factura.numero} ya está cobrada.")

    if factura.estado == Factura.Estado.ANULADA:
        raise ValueError(f"La factura {factura.numero} está anulada y no puede modificarse.")

    factura.estado      = Factura.Estado.COBRADA
    factura.fecha_cobro = fecha_cobro or timezone.now().date()
    factura.save()

    return factura


@transaction.atomic
def anular_factura(factura_id, observacion='', usuario=None):
    """
    Anula una factura presentada. No se puede anular si ya está cobrada.
    """
    try:
        factura = Factura.objects.get(pk=factura_id)
    except Factura.DoesNotExist:
        raise ValueError(f"No existe la factura #{factura_id}.")

    if factura.estado == Factura.Estado.COBRADA:
        raise ValueError(
            f"La factura {factura.numero} ya fue cobrada y no puede anularse."
        )

    factura.estado        = Factura.Estado.ANULADA
    factura.observaciones = observacion
    factura.save()

    return factura


# ──────────────────────────────────────────────────────────────────
# 6. CONSULTAS — para alimentar el dashboard y las vistas
# ──────────────────────────────────────────────────────────────────

def honorarios_sin_factura(app_origen=None):
    """
    Devuelve honorarios que aún no tienen factura asociada.
    Opcionalmente filtrado por app_origen.
    """
    qs = HonorarioLiquidacion.objects.filter(factura_item__isnull=True)
    if app_origen:
        qs = qs.filter(app_origen=app_origen)
    return qs.order_by('-periodo')


def liquidaciones_marchiquita_sin_factura():
    """
    Devuelve Liquidacion de tasas_marchiquita que todavía no tienen
    factura registrada en cobros.
    """
    from tasas_marchiquita.models import Liquidacion as LiquidacionMarchiquita

    ids_con_factura = (
        HonorarioLiquidacion.objects
        .filter(
            app_origen=HonorarioLiquidacion.AppOrigen.TASAS_MARCHIQUITA,
            liquidacion_marchiquita_id__isnull=False,
            factura_item__isnull=False,
        )
        .values_list('liquidacion_marchiquita_id', flat=True)
    )

    return (
        LiquidacionMarchiquita.objects
        .exclude(pk__in=list(ids_con_factura))
        .select_related('importacion')
        .order_by('-importacion__periodo')
    )


def cuotas_sin_honorario():
    """
    Devuelve CuotaPlan de tasacartel con pagado=True que aún no tienen
    HonorarioCuotaItem asociado.
    Se ejecuta sobre la BD 'carteles'.
    """
    from tasacartel.models import CuotaPlan

    ids_con_honorario = HonorarioCuotaItem.objects.values_list('cuota_id', flat=True)

    return (
        CuotaPlan.objects
        .using('carteles')
        .filter(pagado=True)
        .exclude(pk__in=list(ids_con_honorario))
        .select_related('plan__liquidacion__cartel', 'plan__liquidacion__propietario_cartel')
        .order_by('-fecha_pago')
    )


def liquidaciones_contado_sin_honorario():
    """
    Devuelve Liquidacion de tasacartel con estado='pagada' y sin PlanDePago,
    que aún no tienen honorario de contado generado.
    Se ejecuta sobre la BD 'carteles'.
    """
    from tasacartel.models import Liquidacion as LiquidacionCartel

    ids_con_honorario = (
        HonorarioLiquidacion.objects
        .filter(
            app_origen=HonorarioLiquidacion.AppOrigen.TASACARTEL,
            tipo_origen=HonorarioLiquidacion.TipoOrigen.CONTADO,
        )
        .values_list('liquidacion_tasacartel_id', flat=True)
    )

    return (
        LiquidacionCartel.objects
        .using('carteles')
        .filter(estado='pagada')
        .filter(plan_de_pago__isnull=True)
        .exclude(pk__in=list(ids_con_honorario))
        .select_related('cartel', 'propietario_cartel')
        .order_by('-fecha_determinacion')
    )


def resumen_facturacion(app_origen=None):
    """
    Totales de facturación para el dashboard.
    """
    from django.db.models import Sum, Count, Q

    qs_honorarios = HonorarioLiquidacion.objects.all()
    qs_facturas   = Factura.objects.all()

    if app_origen:
        qs_honorarios = qs_honorarios.filter(app_origen=app_origen)
        qs_facturas   = qs_facturas.filter(app_origen=app_origen)

    total_honorarios = qs_honorarios.aggregate(
        t=Sum('monto_honorario'))['t'] or Decimal('0')

    agg = qs_facturas.aggregate(
        total_facturado      = Sum('monto_total'),
        total_cobrado        = Sum('monto_total', filter=Q(estado='COBRADA')),
        facturas_presentadas = Count('id', filter=Q(estado='PRESENTADA')),
        facturas_cobradas    = Count('id', filter=Q(estado='COBRADA')),
    )

    total_facturado      = agg['total_facturado']      or Decimal('0')
    total_cobrado        = agg['total_cobrado']        or Decimal('0')
    facturas_presentadas = agg['facturas_presentadas'] or 0
    facturas_cobradas    = agg['facturas_cobradas']    or 0

    return {
        'total_honorarios'     : total_honorarios,
        'total_facturado'      : total_facturado,
        'total_cobrado'        : total_cobrado,
        'total_pendiente_cobro': total_facturado - total_cobrado,
        'facturas_presentadas' : facturas_presentadas,
        'facturas_cobradas'    : facturas_cobradas,
    }
