from django.db import models
from django.utils.translation import gettext_lazy as _


class ConfiguracionHonorario(models.Model):

    class AppOrigen(models.TextChoices):
        TASACARTEL        = 'tasacartel',        'Tasa Cartel'
        TASAS_MARCHIQUITA = 'tasas_marchiquita', 'Tasas Marchiquita'

    app_origen      = models.CharField(max_length=20, choices=AppOrigen.choices)
    porcentaje      = models.DecimalField(max_digits=5, decimal_places=2)
    vigente_desde   = models.DateField()
    creado_por      = models.ForeignKey(
                          "auth.User", null=True, blank=True,
                          on_delete=models.SET_NULL,
                          db_constraint=False)
    creado_en       = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label           = 'cobros_publivial'
        verbose_name        = _("Configuración de honorario")
        verbose_name_plural = _("Configuraciones de honorario")
        ordering            = ['app_origen', '-vigente_desde']
        unique_together     = [('app_origen', 'vigente_desde')]

    def __str__(self):
        return f"{self.get_app_origen_display()} — {self.porcentaje}% desde {self.vigente_desde}"


class HonorarioLiquidacion(models.Model):

    class AppOrigen(models.TextChoices):
        TASACARTEL        = 'tasacartel',        'Tasa Cartel'
        TASAS_MARCHIQUITA = 'tasas_marchiquita', 'Tasas Marchiquita'

    class TipoOrigen(models.TextChoices):
        CONTADO     = 'CONTADO',     'Contado'
        CUOTAS      = 'CUOTAS',      'Cuotas'
        MARCHIQUITA = 'MARCHIQUITA', 'Tasas Marchiquita'

    app_origen      = models.CharField(max_length=20, choices=AppOrigen.choices)
    tipo_origen     = models.CharField(max_length=15, choices=TipoOrigen.choices)

    # FKs cross-database: db_constraint=False porque están en BDs distintas
    liquidacion_tasacartel_id = models.IntegerField(
                          null=True, blank=True,
                          help_text="ID de tasacartel.Liquidacion")
    liquidacion_marchiquita_id = models.IntegerField(
                          null=True, blank=True,
                          help_text="ID de tasas_marchiquita.Liquidacion")

    periodo         = models.DateField(help_text="Primer día del mes")
    base_calculo    = models.DecimalField(max_digits=14, decimal_places=2,
                          help_text="Monto cobrado sobre el que se aplica el porcentaje")
    porcentaje_aplicado = models.DecimalField(max_digits=5, decimal_places=2)
    monto_honorario = models.DecimalField(max_digits=14, decimal_places=2)

    generado_en     = models.DateTimeField(auto_now_add=True)
    generado_por    = models.ForeignKey(
                          "auth.User", null=True, blank=True,
                          on_delete=models.SET_NULL,
                          db_constraint=False,
                          related_name='honorarios_generados')

    class Meta:
        app_label           = 'cobros_publivial'
        verbose_name        = _("Honorario de liquidación")
        verbose_name_plural = _("Honorarios de liquidación")
        ordering            = ['-periodo']
        # Evita duplicar honorario para la misma liquidación de marchiquita
        constraints = [
            models.UniqueConstraint(
                fields=['liquidacion_marchiquita_id'],
                condition=models.Q(liquidacion_marchiquita_id__isnull=False),
                name='unique_honorario_marchiquita'
            ),
            # Para tasacartel contado, un honorario por liquidación
            models.UniqueConstraint(
                fields=['liquidacion_tasacartel_id', 'tipo_origen'],
                condition=models.Q(
                    liquidacion_tasacartel_id__isnull=False,
                    tipo_origen='CONTADO'
                ),
                name='unique_honorario_tasacartel_contado'
            ),
        ]

    def __str__(self):
        return (f"{self.get_app_origen_display()} | "
                f"{self.periodo.strftime('%B %Y')} | "
                f"$ {self.monto_honorario:,.2f}")


class HonorarioCuotaItem(models.Model):
    """
    Cuotas de tasacartel incluidas en un honorario de tipo CUOTAS.
    Reemplaza el ManyToMany para poder trabajar cross-database.
    """
    honorario       = models.ForeignKey(
                          HonorarioLiquidacion, on_delete=models.CASCADE,
                          related_name='cuota_items')
    cuota_id        = models.IntegerField(help_text="ID de tasacartel.Cuota")
    monto_cuota     = models.DecimalField(max_digits=14, decimal_places=2,
                          help_text="Snapshot del monto al momento de generar el honorario")

    class Meta:
        app_label       = 'cobros_publivial'
        unique_together = [('honorario', 'cuota_id')]

    def __str__(self):
        return f"Cuota {self.cuota_id} — Honorario {self.honorario_id}"


class Factura(models.Model):

    class Estado(models.TextChoices):
        PRESENTADA = 'PRESENTADA', 'Presentada'
        COBRADA    = 'COBRADA',    'Cobrada'
        RECHAZADA  = 'RECHAZADA',  'Rechazada'
        ANULADA    = 'ANULADA',    'Anulada'

    numero          = models.CharField(max_length=50, unique=True)
    app_origen      = models.CharField(max_length=20,
                          help_text="Todas las líneas deben ser de la misma app")
    fecha_emision   = models.DateField()
    fecha_cobro     = models.DateField(null=True, blank=True)
    monto_total     = models.DecimalField(max_digits=14, decimal_places=2)
    estado          = models.CharField(max_length=15, choices=Estado.choices,
                          default=Estado.PRESENTADA)
    observaciones   = models.TextField(blank=True)
    archivo_pdf     = models.BinaryField(null=True, blank=True)
    creado_por      = models.ForeignKey(
                          "auth.User", null=True, blank=True,
                          on_delete=models.SET_NULL,
                          db_constraint=False,
                          related_name='facturas_creadas')
    creado_en       = models.DateTimeField(auto_now_add=True)
    actualizado_en  = models.DateTimeField(auto_now=True)

    class Meta:
        app_label           = 'cobros_publivial'
        verbose_name        = 'Factura'
        verbose_name_plural = 'Facturas'
        ordering            = ['-fecha_emision']

    def __str__(self):
        return f"Factura {self.numero} — {self.get_estado_display()} — $ {self.monto_total:,.2f}"


class FacturaItem(models.Model):
    factura     = models.ForeignKey(
                      Factura, on_delete=models.CASCADE,
                      related_name='items')
    honorario   = models.OneToOneField(
                      HonorarioLiquidacion, on_delete=models.PROTECT,
                      related_name='factura_item',
                      help_text="Un honorario no puede aparecer en dos facturas")
    monto       = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        app_label = 'cobros_publivial'

    def __str__(self):
        return f"{self.factura.numero} — {self.honorario}"
    
