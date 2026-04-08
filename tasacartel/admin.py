from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import ValorTasaAnual, Liquidacion, LiquidacionPeriodo, PlanDePago, CuotaPlan


# ════════════════════════════════════════════════════════════════════════════
# VALOR DE TASA ANUAL
# ════════════════════════════════════════════════════════════════════════════

@admin.register(ValorTasaAnual)
class ValorTasaAnualAdmin(admin.ModelAdmin):
    list_display  = ["anio", "valor_m2", "recargo_iluminado_pct", "observaciones", "actualizado"]
    list_editable = ["valor_m2", "recargo_iluminado_pct"]
    ordering      = ["-anio"]
    search_fields = ["anio"]


# ════════════════════════════════════════════════════════════════════════════
# INLINES
# ════════════════════════════════════════════════════════════════════════════

class LiquidacionPeriodoInline(admin.TabularInline):
    model  = LiquidacionPeriodo
    extra  = 1
    fields = [
        "anio_fiscal", "valor_tasa", "valor_m2_aplicado",
        "subtotal_base", "recargo_iluminado", "descuento_ruta63",
        "subtotal_con_recargos", "meses_mora", "tasa_mora_mensual",
        "interes_mora", "total_periodo",
    ]
    readonly_fields = [
        "subtotal_base", "recargo_iluminado", "descuento_ruta63",
        "subtotal_con_recargos", "meses_mora", "interes_mora", "total_periodo",
    ]


class CuotaPlanInline(admin.TabularInline):
    model   = CuotaPlan
    extra   = 0
    fields  = ["nro_cuota", "monto_capital", "monto_interes", "monto_total", "fecha_vencimiento", "pagado", "fecha_pago"]
    readonly_fields = ["nro_cuota", "monto_capital", "monto_interes", "monto_total"]


# ════════════════════════════════════════════════════════════════════════════
# LIQUIDACIÓN
# ════════════════════════════════════════════════════════════════════════════

@admin.register(Liquidacion)
class LiquidacionAdmin(admin.ModelAdmin):
    list_display = [
        "id", "cartel_link", "estado_badge", "version",
        "superficie_m2", "monto_base_total", "monto_mora_total", "monto_total",
        "fecha_determinacion", "generado_por",
    ]
    list_filter    = ["estado", "es_iluminado", "aplica_descuento_ruta63", "sobre_ruta2"]
    search_fields  = [
        "cartel__id",
        "propietario_cartel__apellido", "propietario_cartel__razon_social",
        "propietario_terreno__apellido", "propietario_terreno__razon_social",
        "empresa_publicista__apellido",  "empresa_publicista__razon_social",
    ]
    readonly_fields = [
        "monto_base_total", "monto_mora_total", "monto_total",
        "version", "liquidacion_origen", "creado", "actualizado",
    ]
    inlines = [LiquidacionPeriodoInline]

    fieldsets = (
        ("Cartel", {
            "fields": ("cartel",),
        }),
        ("Responsables (snapshot al momento de la determinación)", {
            "fields": (
                "propietario_cartel",
                "propietario_terreno",
                "empresa_publicista",
            ),
        }),
        ("Superficie y condiciones", {
            "fields": (
                "superficie_m2",
                "es_iluminado",
                "aplica_descuento_ruta63",
                "km_ruta63",
                "sobre_ruta2",
            ),
        }),
        ("Totales calculados", {
            "fields": (
                "monto_base_total",
                "monto_mora_total",
                "monto_total",
            ),
        }),
        ("Estado y versionado", {
            "fields": (
                "estado",
                "version",
                "liquidacion_origen",
                "fecha_determinacion",
                "generado_por",
                "observaciones",
            ),
        }),
        ("Auditoría", {
            "classes": ("collapse",),
            "fields": ("creado", "actualizado"),
        }),
    )

    def cartel_link(self, obj):
        url = reverse("admin:carteles_cartel_change", args=[obj.cartel_id])
        return format_html('<a href="{}">Cartel #{}</a>', url, obj.cartel_id)
    cartel_link.short_description = "Cartel"

    def estado_badge(self, obj):
        colores = {
            "borrador":   "#aaa",
            "notificada": "#2196F3",
            "conformada": "#4CAF50",
            "en_plan":    "#FF9800",
            "pagada":     "#388E3C",
            "objetada":   "#F44336",
        }
        color = colores.get(obj.estado, "#aaa")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px">{}</span>',
            color, obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"

    def save_model(self, request, obj, form, change):
        # Poblar snapshot desde el cartel si es nuevo
        if not change:
            obj.poblar_desde_cartel()
            obj.generado_por = request.user
        # Incrementar versión si se edita
        elif change and "superficie_m2" in form.changed_data:
            obj.version += 1
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Recalcular totales después de guardar los períodos
        form.instance.recalcular_totales()


# ════════════════════════════════════════════════════════════════════════════
# PLAN DE PAGO
# ════════════════════════════════════════════════════════════════════════════

@admin.register(PlanDePago)
class PlanDePagoAdmin(admin.ModelAdmin):
    list_display = [
        "id", "liquidacion", "fecha_suscripcion",
        "monto_deuda_base", "monto_anticipo", "cantidad_cuotas",
        "tasa_financiacion_mensual", "estado",
    ]
    list_filter   = ["estado"]
    search_fields = ["liquidacion__id", "liquidacion__cartel__id"]
    readonly_fields = ["creado", "actualizado"]
    inlines = [CuotaPlanInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Generar cuotas automáticamente al crear el plan
        if not change:
            obj.generar_cuotas()
            # Actualizar estado de la liquidación
            obj.liquidacion.estado = "en_plan"
            obj.liquidacion.save(update_fields=["estado"])
