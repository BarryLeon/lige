from django.contrib import admin

from .models import (
    ConfiguracionHonorario,
    HonorarioLiquidacion,
    HonorarioCuotaItem,
    Factura,
    FacturaItem,
)


@admin.register(ConfiguracionHonorario)
class ConfiguracionHonorarioAdmin(admin.ModelAdmin):
    list_display = ('app_origen', 'porcentaje', 'vigente_desde', 'creado_por')
    list_filter = ('app_origen',)
    ordering = ('app_origen', '-vigente_desde')


@admin.register(HonorarioLiquidacion)
class HonorarioLiquidacionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'app_origen',
        'tipo_origen',
        'periodo',
        'base_calculo',
        'porcentaje_aplicado',
        'monto_honorario',
    )
    list_filter = ('app_origen', 'tipo_origen', 'periodo')
    search_fields = ('liquidacion_tasacartel_id', 'liquidacion_marchiquita_id')


@admin.register(HonorarioCuotaItem)
class HonorarioCuotaItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'honorario', 'cuota_id', 'monto_cuota')
    search_fields = ('cuota_id',)


class FacturaItemInline(admin.TabularInline):
    model = FacturaItem
    extra = 0
    readonly_fields = ('honorario', 'monto')


@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = ('numero', 'app_origen', 'fecha_emision', 'monto_total', 'estado')
    list_filter = ('app_origen', 'estado')
    search_fields = ('numero',)
    inlines = [FacturaItemInline]
