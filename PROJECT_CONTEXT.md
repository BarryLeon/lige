# PROJECT_CONTEXT.md

## Arquitectura de Bases de Datos

- **4 bases de datos**: `default`, `marchiquita`, `carteles`, `cobros_publivial`
- **Router**: `lige.routers.ClientesRouter` dirige consultas por `app_label`
- **Relaciones cruzadas**: Usan `db_constraint=False` para evitar FKs entre DBs

## Modelos Principales

### `carteles` (base: `carteles`)
- `Cartel`: 
  - FK a `Parcela` (nullable), `Persona` (propietario, nullable)
  - Campos: `foto`, `lat`, `lon`, `superficie_m2`, `estado_procesamiento`, `estado_registro`
  - Relaciones: `historial_publicidad` (1:M), `liquidaciones` (1:M)
- `Parcela`:
  - FK a `Persona` (propietario_terreno, nullable)
  - Campos: `circunscripcion`, `seccion`, `chacra`, `parcela_nro`, `direccion`
- `Persona`:
  - Campos: `tipo` (fisica/juridica), `cuit_dni`, `apellido`, `nombre`, `razon_social`
- `HistorialPublicidad`:
  - FK a `Cartel`, FK a `Persona` (empresa)
  - Campos: `fecha_desde`, `fecha_hasta` (null = activa)

### `tasacartel` (base: `carteles`)
- `Liquidacion`:
  - FK a `Cartel`
  - FKs snapshot: `propietario_cartel`, `propietario_terreno`, `empresa_publicista` (todos nullable)
  - Campos: `superficie_m2`, `es_iluminado`, `aplica_descuento_ruta63`, `monto_total`, `estado`, `version`, `liquidacion_origen` (auto-referencia)
  - Relaciones: `periodos` (1:M)
- `LiquidacionPeriodo`:
  - FK a `Liquidacion`, FK a `ValorTasaAnual`
  - Campos: `anio_fiscal`, `valor_m2_aplicado`, `subtotal_base`, `interes_mora`, `total_periodo`
- `ValorTasaAnual`:
  - Campos: `anio`, `valor_m2`, `recargo_iluminado_pct`
- `PlanDePago`:
  - FK a `Liquidacion` (1:1)
  - Campos: `cantidad_cuotas`, `tasa_financiacion_mensual`, `estado`
  - Relaciones: `cuotas` (1:M)
- `CuotaPlan`:
  - FK a `PlanDePago`
  - Campos: `nro_cuota`, `monto_capital`, `monto_interes`, `monto_total`, `fecha_vencimiento`, `pagado`, `fecha_pago`

### `tasas_marchiquita` (base: `marchiquita`)
- `ArchivoImportacion`:
  - Campos: `archivo` (BinaryField), `periodo`, `procesado`, `revertido`
  - Relaciones: `deudas` (1:M), `cuotas` (1:M), `liquidacion` (1:1)
- `ResponsablePago`:
  - Campos: `resp_pago` (único), `nombre`, `documento`, `email`
  - Relaciones: `parcelas` (1:M)
- `Parcela`:
  - FK a `ResponsablePago`
  - Campos: `nro_inmueble`, `circuns`, `fraccion_nro`, `parcela_nro`, `parcela_let`, `subparcela`
- `Deuda`:
  - FK a `Parcela`, FK a `ArchivoImportacion`
  - Campos: `valor_total_deuda`, `pago_total_deuda`, `tiene_plan_cuotas`, `anticipo`, `cantidad_cuotas`, `estado` (VIGENTE/EN_PLAN/CANCELADA)
  - Relaciones: `cuotas` (1:M)
- `Cuota`:
  - FK a `Deuda`, FK a `ArchivoImportacion`
  - Campos: `numero_cuota`, `monto_pagado`, `estado` (PENDIENTE/PAGA/VENCIDA)
- `Liquidacion`:
  - FK a `ArchivoImportacion` (1:1)
  - Campos: `total_cobrado`, `comision` (25% de total_cobrado)

### `cobros_publivial` (base: `default`)
- `ConfiguracionHonorario`:
  - Campos: `app_origen` (tasacartel/tasas_marchiquita), `porcentaje`, `vigente_desde`
- `HonorarioLiquidacion`:
  - Campos: `app_origen`, `tipo_origen` (CONTADO/CUOTAS/MARCHIQUITA), `periodo`, `base_calculo`, `porcentaje_aplicado`, `monto_honorario`
  - FKs cruzadas: `liquidacion_tasacartel_id` (int), `liquidacion_marchiquita_id` (int)
  - Relaciones: `cuota_items` (1:M)
- `HonorarioCuotaItem`:
  - FK a `HonorarioLiquidacion`
  - Campos: `cuota_id` (int, referencia a tasacartel.CuotaPlan)
- `Factura`:
  - Campos: `numero`, `app_origen`, `fecha_emision`, `fecha_cobro`, `monto_total`, `estado` (PRESENTADA/COBRADA/RECHAZADA/ANULADA)
  - Relaciones: `items` (1:M)
- `FacturaItem`:
  - FK a `Factura`, FK a `HonorarioLiquidacion` (1:1)
  - Campos: `monto`

## Queries Típicas

### `carteles`
- `Cartel.objects.filter(estado_procesamiento="ok", estado_registro="activo").select_related("parcela", "propietario_cartel")`
- `Cartel.objects.filter(propiedad_cartel__cuit_dni="12345678").prefetch_related("historial_publicidad")`
- `HistorialPublicidad.objects.filter(cartel_id=123, fecha_hasta__isnull=True)`

### `tasacartel`
- `Liquidacion.objects.filter(cartel_id=456).order_by("-version").first()`
- `LiquidacionPeriodo.objects.filter(liquidacion_id=789).aggregate(Sum("total_periodo"))`
- `PlanDePago.objects.filter(liquidacion_id=123, estado="vigente").prefetch_related("cuotas")`
- `CuotaPlan.objects.filter(plan__liquidacion__estado="conformada", pagado=True).exclude(honorariocuotaitem__isnull=False)`

### `tasas_marchiquita`
- `ArchivoImportacion.objects.filter(procesado=True, periodo__year=2024)`
- `Deuda.objects.filter(parcela__nro_inmueble="12345").filter(estado="VIGENTE")`
- `Cuota.objects.filter(deuda__responsable_pago__resp_pago="ABC123").filter(estado="PAGA")`
- `Liquidacion.objects.filter(importacion__periodo="2024-03-01").select_related("importacion")`

### `cobros_publivial`
- `HonorarioLiquidacion.objects.filter(app_origen="tasacartel", tipo_origen="CONTADO", factura_item__isnull=True)`
- `Factura.objects.filter(app_origen="tasas_marchiquita", estado="PRESENTADA").prefetch_related("items__honorario")`
- `HonorarioCuotaItem.objects.filter(honorario__liquidacion_tasacartel_id=456)`
- `ConfiguracionHonorario.objects.filter(app_origen="tasacartel", vigente_desde__lte=date.today()).order_by("-vigente_desde").first()`