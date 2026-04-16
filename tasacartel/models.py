from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from decimal import Decimal
from dateutil.relativedelta import relativedelta


# ════════════════════════════════════════════════════════════════════════════
# VALOR DE TASA ANUAL
# Tabla de valores históricos: valor por m² por año fiscal.
# Se carga y edita desde una vista de administración.
# ════════════════════════════════════════════════════════════════════════════

class ValorTasaAnual(models.Model):
    """
    Valor del metro cuadrado por año fiscal.
    Art. 9° Ordenanza Impositiva, inc. 2) Publicidad foránea.
    Cargado con valores históricos 2021–2026 y ampliable año a año.
    """
    anio = models.IntegerField(
        unique=True,
        verbose_name="Año fiscal",
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
    )
    valor_m2 = models.DecimalField(
        max_digits=12, decimal_places=2,
        verbose_name="Valor por m² ($)",
    )
    recargo_iluminado_pct = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Recargo publicidad iluminada (%)",
        help_text="Desde 2026: 25%. Años anteriores: 0%.",
    )
    observaciones = models.TextField(blank=True, null=True)

    creado      = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Valor de tasa anual"
        verbose_name_plural = "Valores de tasa anual"
        ordering = ["-anio"]

    def __str__(self):
        return f"Año {self.anio} — ${self.valor_m2}/m²"


# ════════════════════════════════════════════════════════════════════════════
# LIQUIDACIÓN (cabecera)
# Una liquidación puede agrupar varios años adeudados para un cartel.
# Es editable (historial de versiones por contribuyente).
# ════════════════════════════════════════════════════════════════════════════

class Liquidacion(models.Model):
    """
    Cabecera de la liquidación de tasa municipal por cartel publicitario.

    Responsabilidad solidaria: los tres responsables (propietario del cartel,
    propietario del terreno, empresa publicista) responden por el total.
    Se registra snapshot de cada uno al momento de la determinación.

    Una liquidación puede cubrir múltiples años fiscales (ver LiquidacionPeriodo).
    """

    # ── Referencia al cartel ─────────────────────────────────────────────────
    cartel = models.ForeignKey(
        "carteles.Cartel",
        on_delete=models.PROTECT,
        related_name="liquidaciones",
        limit_choices_to={
            "estado_procesamiento": "ok",
            "estado_registro": "activo",
        },
        verbose_name="Cartel",
    )

    # ── Snapshot de responsables al momento de la determinación ─────────────
    # Se copian para que el historial no se altere si cambian los datos del cartel.
    propietario_cartel = models.ForeignKey(
        "carteles.Persona",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="liquidaciones_como_propietario_cartel",
        verbose_name="Propietario del cartel (al momento)",
    )
    propietario_terreno = models.ForeignKey(
        "carteles.Persona",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="liquidaciones_como_propietario_terreno",
        verbose_name="Propietario del terreno (al momento)",
    )
    empresa_publicista = models.ForeignKey(
        "carteles.Persona",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="liquidaciones_como_empresa",
        verbose_name="Empresa publicista (al momento)",
    )

    # ── Superficie liquidada ─────────────────────────────────────────────────
    # Se copia del cartel pero puede editarse si el contribuyente objeta.
    superficie_m2 = models.DecimalField(
        max_digits=10, decimal_places=4,
        verbose_name="Superficie liquidada (m²)",
        help_text="Copiado del cartel. Editable si el contribuyente objeta la medición.",
    )

    # ── Condiciones especiales que afectan el cálculo ────────────────────────
    es_iluminado = models.BooleanField(
        default=False,
        verbose_name="¿Publicidad iluminada?",
        help_text="Si es True y el año >= 2026, se aplica recargo del 25%.",
    )
    aplica_descuento_ruta63 = models.BooleanField(
        default=False,
        verbose_name="¿Aplica descuento Ruta 63?",
        help_text="Carteles en Ruta 63 desde km 5 en adelante: 30% de bonificación.",
    )
    km_ruta63 = models.DecimalField(
        max_digits=7, decimal_places=2,
        null=True, blank=True,
        verbose_name="Kilómetro en Ruta 63",
        help_text="Informado por GPS o manualmente en observaciones del cartel.",
    )
    sobre_ruta2 = models.BooleanField(
        default=False,
        verbose_name="¿Está sobre Ruta 2?",
        help_text="Carteles sobre Ruta 2: sin descuento.",
    )

    # ── Totales calculados ───────────────────────────────────────────────────
    monto_base_total = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Monto base total ($)",
        help_text="Suma de subtotales de todos los períodos, sin mora.",
    )
    monto_mora_total = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Intereses por mora total ($)",
    )
    monto_total = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Monto total liquidado ($)",
        help_text="Monto base + mora. Es el monto de la deuda actualizada.",
    )

    # ── Estado y ciclo de vida ───────────────────────────────────────────────
    ESTADO_CHOICES = [
        ("borrador",    "Borrador"),
        ("notificada",  "Notificada al contribuyente"),
        ("conformada",  "Conformada (contribuyente de acuerdo)"),
        ("en_plan",     "En plan de pagos"),
        ("pagada",      "Pagada"),
        ("objetada",    "Objetada por el contribuyente"),
    ]
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default="borrador",
    )

    # ── Versionado ───────────────────────────────────────────────────────────
    version = models.PositiveIntegerField(
        default=1,
        verbose_name="Versión",
        help_text="Se incrementa cada vez que se edita la liquidación.",
    )
    liquidacion_origen = models.ForeignKey(
        "self",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="revisiones",
        verbose_name="Liquidación original",
        help_text="Si es una revisión, apunta a la liquidación que le dio origen.",
    )

    # ── Auditoría ────────────────────────────────────────────────────────────
    # NOTA: Se usa IntegerField en lugar de ForeignKey a auth.User para evitar
    # JOINs cross-database (Liquidacion vive en 'tasas_carteles', auth en 'default').
    # El acceso al objeto User se hace a través de la propiedad `generado_por`.
    generado_por_id_val = models.IntegerField(
        null=True, blank=True,
        db_column="generado_por_id",  # mantiene el mismo nombre de columna en BD
        verbose_name="Generado por (user id)",
    )

    @property
    def generado_por(self):
        from django.contrib.auth import get_user_model
        if not self.generado_por_id_val:
            return None
        return get_user_model().objects.using("default").filter(
            pk=self.generado_por_id_val
        ).first()

    @generado_por.setter
    def generado_por(self, user):
        self.generado_por_id_val = user.pk if user else None

    fecha_determinacion = models.DateField(
        default=timezone.now,
        verbose_name="Fecha de determinación",
    )
    observaciones = models.TextField(blank=True, null=True)

    creado      = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Liquidación"
        verbose_name_plural = "Liquidaciones"
        ordering = ["-fecha_determinacion", "-version"]

    def __str__(self):
        return (
            f"Liquidación #{self.id} — Cartel #{self.cartel_id} "
            f"v{self.version} [{self.get_estado_display()}]"
        )

    # ── Métodos de negocio ───────────────────────────────────────────────────

    def recalcular_totales(self):
        """
        Recalcula monto_base_total, monto_mora_total y monto_total
        sumando todos los LiquidacionPeriodo asociados.
        Llamar después de guardar/modificar períodos.
        """
        periodos = self.periodos.all()
        self.monto_base_total = sum(p.subtotal_con_recargos for p in periodos)
        self.monto_mora_total = sum(p.interes_mora for p in periodos)
        self.monto_total = self.monto_base_total + self.monto_mora_total
        self.save(update_fields=["monto_base_total", "monto_mora_total", "monto_total"])

    def poblar_desde_cartel(self):
        """
        Copia los datos del cartel a los campos snapshot de la liquidación.
        Llamar al crear una nueva liquidación.
        """
        cartel = self.cartel
        self.superficie_m2 = Decimal(str(cartel.superficie_m2 or 0))
        self.propietario_cartel = cartel.propietario_cartel
        if cartel.parcela:
            self.propietario_terreno = cartel.parcela.propietario_terreno
        pub = cartel.publicidad_actual()
        if pub:
            self.empresa_publicista = pub.empresa


# ════════════════════════════════════════════════════════════════════════════
# LIQUIDACIÓN PERÍODO
# Detalle año por año dentro de una liquidación.
# ════════════════════════════════════════════════════════════════════════════

class LiquidacionPeriodo(models.Model):
    """
    Detalle de un año fiscal dentro de una liquidación.
    Cada período registra su propio cálculo: subtotal, recargos y mora.
    """
    liquidacion = models.ForeignKey(
        Liquidacion,
        on_delete=models.CASCADE,
        related_name="periodos",
        verbose_name="Liquidación",
    )
    valor_tasa = models.ForeignKey(
        ValorTasaAnual,
        on_delete=models.PROTECT,
        related_name="periodos_liquidacion",
        verbose_name="Valor de tasa del año",
    )
    anio_fiscal = models.IntegerField(
        verbose_name="Año fiscal",
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
    )

    # ── Snapshot del valor aplicado ──────────────────────────────────────────
    valor_m2_aplicado = models.DecimalField(
        max_digits=12, decimal_places=2,
        verbose_name="Valor m² aplicado ($)",
        help_text="Snapshot del valor al momento de la liquidación.",
    )

    # ── Cálculo del período ──────────────────────────────────────────────────
    subtotal_base = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Subtotal base ($)",
        help_text="superficie_m2 × valor_m2_aplicado",
    )
    recargo_iluminado = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Recargo iluminado ($)",
    )
    descuento_ruta63 = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Descuento Ruta 63 ($)",
        help_text="Valor positivo: se resta del subtotal.",
    )
    subtotal_con_recargos = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Subtotal con recargos ($)",
        help_text="subtotal_base + recargo_iluminado − descuento_ruta63",
    )

    # ── Mora ─────────────────────────────────────────────────────────────────
    # Interés simple: deuda × meses_mora × 0.04
    # Referencia: vencimiento al 31/12 del año fiscal.
    meses_mora = models.IntegerField(
        default=0,
        verbose_name="Meses de mora",
        help_text="Calculado desde dic. del año fiscal hasta la fecha de determinación.",
    )
    tasa_mora_mensual = models.DecimalField(
        max_digits=5, decimal_places=4,
        default=Decimal("0.0400"),
        verbose_name="Tasa mora mensual (fracción)",
        help_text="Por defecto 0.04 (4% mensual). Editable para casos especiales.",
    )
    interes_mora = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Interés por mora ($)",
        help_text="subtotal_con_recargos × meses_mora × tasa_mora_mensual",
    )
    total_periodo = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Total del período ($)",
        help_text="subtotal_con_recargos + interes_mora",
    )

    class Meta:
        verbose_name = "Período liquidado"
        verbose_name_plural = "Períodos liquidados"
        ordering = ["anio_fiscal"]
        unique_together = [("liquidacion", "anio_fiscal")]

    def __str__(self):
        return f"Año {self.anio_fiscal} — Liq. #{self.liquidacion_id} — ${self.total_periodo}"

    def calcular(self):
        """
        Ejecuta el cálculo completo del período y guarda los campos derivados.
        Debe llamarse después de asignar liquidacion, valor_tasa y anio_fiscal.
        """
        superficie = self.liquidacion.superficie_m2
        valor_m2   = self.valor_m2_aplicado

        # Subtotal base
        self.subtotal_base = superficie * valor_m2

        # Recargo iluminado (solo si aplica y año >= 2026)
        self.recargo_iluminado = Decimal("0.00")
        if self.liquidacion.es_iluminado and self.anio_fiscal >= 2026:
            pct = self.valor_tasa.recargo_iluminado_pct / Decimal("100")
            self.recargo_iluminado = self.subtotal_base * pct

        # Descuento Ruta 63 (30%)
        self.descuento_ruta63 = Decimal("0.00")
        if self.liquidacion.aplica_descuento_ruta63 and not self.liquidacion.sobre_ruta2:
            self.descuento_ruta63 = self.subtotal_base * Decimal("0.30")

        # Subtotal con recargos
        self.subtotal_con_recargos = (
            self.subtotal_base
            + self.recargo_iluminado
            - self.descuento_ruta63
        )

        # Mora
        import datetime
        fecha_det = self.liquidacion.fecha_determinacion

        if isinstance(fecha_det, datetime.datetime):
            fecha_det = fecha_det.date()

        anio_actual = fecha_det.year

        
        #  AÑO ACTUAL → contar mes parcial
        if self.anio_fiscal == anio_actual:
            self.meses_mora = fecha_det.month

        #  AÑOS ANTERIORES → 12 meses
        elif self.anio_fiscal < anio_actual:
            self.meses_mora = 12 + fecha_det.month  # 12 meses completos + mes parcial del año actual

        # 🔥 FUTURO
        else:
            self.meses_mora = 0
       
        self.interes_mora = (
            self.subtotal_con_recargos
            * self.meses_mora
            * self.tasa_mora_mensual
        )

        self.total_periodo = self.subtotal_con_recargos + self.interes_mora

# ════════════════════════════════════════════════════════════════════════════
# PLAN DE PAGO
# Se confecciona sobre una liquidación conformada.
# Cuota 1 = anticipo sin interés. Cuotas 2..N llevan interés de financiación.
# ════════════════════════════════════════════════════════════════════════════

class PlanDePago(models.Model):
    """
    Plan de pagos sobre una liquidación conformada.
    El plan se hace siempre sobre la deuda actualizada (monto_total de la liquidación).
    """
    liquidacion = models.OneToOneField(
        Liquidacion,
        on_delete=models.PROTECT,
        related_name="plan_de_pago",
        verbose_name="Liquidación",
        limit_choices_to={"estado__in": ["conformada", "en_plan"]},
    )

    fecha_suscripcion = models.DateField(
        default=timezone.now,
        verbose_name="Fecha de suscripción",
    )

    # Snapshot de la deuda al momento de suscribir el plan
    monto_deuda_base = models.DecimalField(
        max_digits=14, decimal_places=2,
        verbose_name="Deuda base del plan ($)",
        help_text="Snapshot de liquidacion.monto_total al momento de suscribir.",
    )

    # Anticipo (cuota 1): no paga interés
    monto_anticipo = models.DecimalField(
        max_digits=14, decimal_places=2,
        verbose_name="Anticipo / Cuota 1 ($)",
        help_text="No paga interés de financiación.",
    )

    cantidad_cuotas = models.PositiveIntegerField(
        verbose_name="Cantidad de cuotas (sin contar el anticipo)",
        help_text="Cuotas 2..N, todas con interés de financiación.",
    )

    # Tasa de financiación configurable al momento de suscribir
    tasa_financiacion_mensual = models.DecimalField(
        max_digits=5, decimal_places=4,
        verbose_name="Tasa de financiación mensual (fracción)",
        help_text="Interés simple. Ej: 0.04 para 4% mensual.",
    )

    ESTADO_CHOICES = [
        ("vigente",    "Vigente"),
        ("cumplido",   "Cumplido"),
        ("caducado",   "Caducado"),
    ]
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default="vigente",
    )

    observaciones = models.TextField(blank=True, null=True)
    creado        = models.DateTimeField(auto_now_add=True)
    actualizado   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plan de pago"
        verbose_name_plural = "Planes de pago"
        ordering = ["-fecha_suscripcion"]

    def __str__(self):
        return (
            f"Plan #{self.id} — Liq. #{self.liquidacion_id} "
            f"({self.cantidad_cuotas} cuotas) [{self.get_estado_display()}]"
        )

    def generar_cuotas(self):
        """
        Genera las CuotaPlan automáticamente al suscribir el plan.

        ✔ NUEVO MODELO:
        - Cuota 1 = anticipo sin interés.
        - Cuotas restantes iguales.
        - Interés simple TOTAL distribuido en todas las cuotas.

        py MODELO ANTERIOR (comentado abajo):
        - Cuotas crecientes por interés acumulado en el tiempo.
        """

        # Limpiar cuotas previas si se regenera
        self.cuotas.all().delete()

        saldo = (self.monto_deuda_base - self.monto_anticipo).quantize(Decimal("0.01"))
        if saldo < 0:
            saldo = Decimal("0.00")

        tasa_mensual = self.tasa_financiacion_mensual.quantize(Decimal("0.0001"))

        cuotas = []

        # ─────────────────────────────────────────────
        # CUOTAS IGUALES INTERES SIMPLE
        # ─────────────────────────────────────────────
        interes_total = (saldo * tasa_mensual * self.cantidad_cuotas).quantize(Decimal("0.01"))
        total_financiado = (saldo + interes_total).quantize(Decimal("0.01"))

        cuota_fija = (
            (total_financiado / self.cantidad_cuotas).quantize(Decimal("0.01"))
            if self.cantidad_cuotas > 0
            else total_financiado
        )

        capital_por_cuota = (
            (saldo / self.cantidad_cuotas).quantize(Decimal("0.01"))
            if self.cantidad_cuotas > 0
            else saldo
        )

        interes_por_cuota = (
            (interes_total / self.cantidad_cuotas).quantize(Decimal("0.01"))
            if self.cantidad_cuotas > 0
            else Decimal("0.00")
        )

        # Cuota 1: anticipo sin interés
        cuotas.append(CuotaPlan(
            plan=self,
            nro_cuota=1,
            monto_capital=self.monto_anticipo,
            monto_interes=Decimal("0.00"),
            monto_total=self.monto_anticipo,
            fecha_vencimiento=self.fecha_suscripcion,
        ))

        # Cuotas 2..N: todas iguales
        for i in range(1, self.cantidad_cuotas + 1):
            fecha_vcto = self.fecha_suscripcion + relativedelta(months=i)

            cuotas.append(CuotaPlan(
                plan=self,
                nro_cuota=i + 1,
                monto_capital=capital_por_cuota,
                monto_interes=interes_por_cuota,
                monto_total=cuota_fija,
                fecha_vencimiento=fecha_vcto,
            ))

        # ─────────────────────────────────────────────
        #  MODELO ANTERIOR (CUOTAS CRECIENTES INTERESES SOBRE SALDOS)
        # ─────────────────────────────────────────────
        # monto_cuota_base = (
        #     saldo / self.cantidad_cuotas
        #     if self.cantidad_cuotas > 0
        #     else saldo
        # )
        #
        # for i in range(1, self.cantidad_cuotas + 1):
        #     fecha_vcto = self.fecha_suscripcion + relativedelta(months=i)
        #
        #     interes = monto_cuota_base * i * self.tasa_financiacion_mensual
        #
        #     cuotas.append(CuotaPlan(
        #         plan=self,
        #         nro_cuota=i + 1,
        #         monto_capital=monto_cuota_base,
        #         monto_interes=interes,
        #         monto_total=monto_cuota_base + interes,
        #         fecha_vencimiento=fecha_vcto,
        #     ))
        CuotaPlan.objects.bulk_create(cuotas)


# ════════════════════════════════════════════════════════════════════════════
# CUOTA DEL PLAN
# ════════════════════════════════════════════════════════════════════════════

class CuotaPlan(models.Model):
    """
    Cuota individual dentro de un plan de pago.
    """
    plan = models.ForeignKey(
        PlanDePago,
        on_delete=models.CASCADE,
        related_name="cuotas",
        verbose_name="Plan de pago",
    )
    nro_cuota         = models.PositiveIntegerField(verbose_name="N° de cuota")
    monto_capital     = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Capital ($)")
    monto_interes     = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Interés ($)")
    monto_total       = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Total cuota ($)")
    fecha_vencimiento = models.DateField(verbose_name="Fecha de vencimiento")
    pagado            = models.BooleanField(default=False, verbose_name="¿Pagada?")
    fecha_pago        = models.DateField(null=True, blank=True, verbose_name="Fecha de pago")
    observaciones     = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Cuota"
        verbose_name_plural = "Cuotas"
        ordering = ["nro_cuota"]
        unique_together = [("plan", "nro_cuota")]

    def __str__(self):
        estado = "✓" if self.pagado else "pendiente"
        return f"Cuota {self.nro_cuota} — Plan #{self.plan_id} — ${self.monto_total} ({estado})"
