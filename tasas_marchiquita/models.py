from django.db import models
from django.utils.translation import gettext_lazy as _


# ──────────────────────────────────────────────────────────────────
# 1. ARCHIVO DE IMPORTACIÓN MENSUAL
#    Guarda el Excel original como binario con un ID único.
#    Cada registro de deuda y cuota referencia al archivo del que provino.
# ──────────────────────────────────────────────────────────────────

class ArchivoImportacion(models.Model):
    archivo         = models.BinaryField()
    nombre_archivo  = models.CharField(max_length=255)
    periodo         = models.DateField(
                          help_text="Primer día del mes al que corresponde. Ej: 2024-03-01")
    importado_en    = models.DateTimeField(auto_now_add=True)
    importado_por   = models.ForeignKey(
                          "auth.User", null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name="importaciones",
                          db_constraint=False)  # Evita error de FK con auth.User
    total_registros = models.PositiveIntegerField(default=0)
    observaciones   = models.TextField(blank=True)
    procesado       = models.BooleanField(default=False,
                          help_text="Indica si el archivo ya fue procesado e incorporado a la BD")
    revertido       = models.BooleanField(default=False,
                          help_text="Indica si el procesamiento fue revertido")
    reemplazado_por = models.ForeignKey(
                          "self", null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name="reemplaza_a",
                          help_text="Archivo corregido que reemplaza a este")

    class Meta:
        verbose_name        = _("Archivo de importación")
        verbose_name_plural = _("Archivos de importación")
        ordering            = ["-periodo"]
        # Permite múltiples archivos por período pero solo uno activo (no revertido)
        unique_together     = [("periodo", "revertido")]

    def __str__(self):
        return f"{self.nombre_archivo} — {self.periodo.strftime('%B %Y')}"


# ──────────────────────────────────────────────────────────────────
# 2. RESPONSABLE DE PAGO (DEUDOR)
#    RESP_PAGO del Excel. Una persona puede tener varias parcelas.
# ──────────────────────────────────────────────────────────────────

class ResponsablePago(models.Model):
    resp_pago       = models.CharField(
                          max_length=100, unique=True,
                          help_text="Valor del campo RESP_PAGO del Excel")
    # Datos adicionales del responsable que puedas tener o agregar luego
    nombre          = models.CharField(max_length=255, blank=True)
    documento       = models.CharField(max_length=20, blank=True)
    email           = models.EmailField(blank=True)
    telefono        = models.CharField(max_length=50, blank=True)
    creado_en       = models.DateTimeField(auto_now_add=True)
    actualizado_en  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = _("Responsable de pago")
        verbose_name_plural = _("Responsables de pago")
        ordering            = ["resp_pago"]

    def __str__(self):
        return f"{self.resp_pago} — {self.nombre or 'Sin nombre'}"


# ──────────────────────────────────────────────────────────────────
# 3. PARCELA
#    Identificada unívocamente por NRO_INMUEBLE +
#    CIRCUNS + FRACCION_NRO + PARCELA_NRO + PARCELA_LET + SUBPARCELA.
#    PAR_CATASTRAL y SUP_TERRENO son datos descriptivos.
# ──────────────────────────────────────────────────────────────────

class Parcela(models.Model):
    # -- Identificación --
    nro_inmueble    = models.CharField(max_length=50)
    circuns         = models.CharField(max_length=50, blank=True,
                                       verbose_name="Circunscripción")
    fraccion_nro    = models.CharField(max_length=50, blank=True)
    parcela_nro     = models.CharField(max_length=50, blank=True)
    parcela_let     = models.CharField(max_length=10, blank=True)
    subparcela      = models.CharField(max_length=50, blank=True)
    uni_funcional   = models.CharField(max_length=50, blank=True,
                                       verbose_name="Unidad funcional")

    # -- Datos descriptivos --
    par_catastral   = models.CharField(max_length=100, blank=True,
                                       verbose_name="Parcela catastral")
    sup_terreno     = models.DecimalField(max_digits=12, decimal_places=2,
                                          null=True, blank=True,
                                          verbose_name="Superficie terreno (m²)")

    # -- Responsable --
    responsable     = models.ForeignKey(
                          ResponsablePago, on_delete=models.PROTECT,
                          related_name="parcelas")

    creado_en       = models.DateTimeField(auto_now_add=True)
    actualizado_en  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = _("Parcela")
        verbose_name_plural = _("Parcelas")
        ordering            = ["nro_inmueble"]
        unique_together     = [("nro_inmueble", "circuns", "fraccion_nro",
                                "parcela_nro", "parcela_let", "subparcela")]

    def __str__(self):
        return (f"{self.nro_inmueble} | "
                f"{self.circuns}-{self.fraccion_nro}-{self.parcela_nro}"
                f"{self.parcela_let}-{self.subparcela}")


# ──────────────────────────────────────────────────────────────────
# 4. DEUDA
#    Estado de la deuda de una parcela en un período mensual.
#    Se crea o actualiza en cada importación.
# ──────────────────────────────────────────────────────────────────

class Deuda(models.Model):

    class Estado(models.TextChoices):
        VIGENTE   = "VIGENTE",   _("Vigente")
        EN_PLAN   = "EN_PLAN",   _("En plan de cuotas")
        CANCELADA = "CANCELADA", _("Cancelada")

    parcela             = models.ForeignKey(
                              Parcela, on_delete=models.PROTECT,
                              related_name="deudas")
    importacion         = models.ForeignKey(
                              ArchivoImportacion, on_delete=models.PROTECT,
                              related_name="deudas",
                              help_text="Archivo del que proviene este estado")

    # -- Campos directos del Excel --
    valor_total_deuda   = models.DecimalField(max_digits=14, decimal_places=2,
                                               verbose_name="VALOR TOTAL_DEUDA")
    pago_total_deuda    = models.DecimalField(max_digits=14, decimal_places=2,
                                               null=True, blank=True,
                                               verbose_name="PAGO_TOTAL_DEUDA")
    tiene_plan_cuotas   = models.BooleanField(default=False,
                                               verbose_name="PLAN DE CUOTAS")

    # -- Solo si tiene_plan_cuotas = True --
    anticipo            = models.DecimalField(max_digits=14, decimal_places=2,
                                               null=True, blank=True,
                                               help_text="Anticipo en efectivo del plan")
    cantidad_cuotas     = models.PositiveSmallIntegerField(
                              null=True, blank=True,
                              verbose_name="CANTIDAD DE CUOTAS")

    # -- Estado calculado --
    estado              = models.CharField(max_length=10, choices=Estado.choices,
                                           default=Estado.VIGENTE)
    creado_en           = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = _("Deuda")
        verbose_name_plural = _("Deudas")
        ordering            = ["-importacion__periodo", "parcela"]
        # Una deuda por parcela por período
        unique_together     = [("parcela", "importacion")]

    def __str__(self):
        return f"{self.parcela} | {self.importacion.periodo.strftime('%B %Y')} | {self.estado}"

    @property
    def saldo_pendiente(self):
        return self.valor_total_deuda - (self.pago_total_deuda or 0) - (self.anticipo or 0) - sum(c.monto_pagado for c in self.cuotas.all())

    @property
    def esta_cancelada(self):
        return self.estado == self.Estado.CANCELADA
    
    @property
    def total_pagado(self):
        return self.valor_total_deuda - self.saldo_pendiente


# ──────────────────────────────────────────────────────────────────
# 5. CUOTA
#    Cada cuota pagada dentro de un plan de cuotas.
#    Cada mes el Excel informa el número de cuota pagada y el monto.
# ──────────────────────────────────────────────────────────────────

class Cuota(models.Model):

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", _("Pendiente")
        PAGA      = "PAGA",      _("Paga")
        VENCIDA   = "VENCIDA",   _("Vencida")

    deuda               = models.ForeignKey(
                              Deuda, on_delete=models.CASCADE,
                              related_name="cuotas")
    importacion         = models.ForeignKey(
                              ArchivoImportacion, on_delete=models.PROTECT,
                              related_name="cuotas",
                              help_text="Archivo en el que se registró este pago")

    # -- Campos directos del Excel --
    numero_cuota        = models.PositiveSmallIntegerField(
                              verbose_name="NUMERO_DE_CUOTA_PAGADA")
    monto_pagado        = models.DecimalField(max_digits=14, decimal_places=2,
                                               verbose_name="MONTO_PAGADO")
    estado              = models.CharField(max_length=10, choices=Estado.choices,
                                           default=Estado.PAGA)
    creado_en           = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = _("Cuota")
        verbose_name_plural = _("Cuotas")
        ordering            = ["deuda", "numero_cuota"]
        # Una cuota no se puede repetir para la misma deuda
        unique_together     = [("deuda", "numero_cuota")]

    def __str__(self):
        return (f"Cuota {self.numero_cuota} — "
                f"{self.deuda.parcela} | {self.estado}")

# ──────────────────────────────────────────────────────────────────
# 6. LIQUIDACION
#    Comisión del 25% calculada sobre lo cobrado en un período.
#    Se genera manualmente desde el dashboard para un archivo procesado.
# ──────────────────────────────────────────────────────────────────

class Liquidacion(models.Model):
    importacion         = models.OneToOneField(
                              ArchivoImportacion, on_delete=models.PROTECT,
                              related_name="liquidacion",
                              help_text="Archivo procesado sobre el que se calcula la liquidación")
    total_pago_deuda    = models.DecimalField(max_digits=14, decimal_places=2,
                                               help_text="Suma de PAGO_TOTAL_DEUDA del período")
    total_anticipos     = models.DecimalField(max_digits=14, decimal_places=2,
                                               help_text="Suma de anticipos del período")
    total_cuotas        = models.DecimalField(max_digits=14, decimal_places=2,
                                               help_text="Suma de montos de cuotas pagadas del período")
    total_cobrado       = models.DecimalField(max_digits=14, decimal_places=2,
                                               help_text="Suma total cobrada (pago + anticipos + cuotas)")
    comision            = models.DecimalField(max_digits=14, decimal_places=2,
                                               help_text="25% del total cobrado")
    pdf                 = models.BinaryField(null=True, blank=True,
                                               help_text="PDF de la liquidación generado")
    generada_en         = models.DateTimeField(auto_now_add=True)
    generada_por        = models.ForeignKey(
                              "auth.User", null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="liquidaciones",
                              db_constraint=False)  # Evita error de FK con auth.User

    class Meta:
        verbose_name        = _("Liquidación")
        verbose_name_plural = _("Liquidaciones")
        ordering            = ["-importacion__periodo"]

    def __str__(self):
        return f"Liquidación {self.importacion.periodo.strftime('%B %Y')} — Comisión: ${self.comision}"