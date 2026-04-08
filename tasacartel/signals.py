from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import LiquidacionPeriodo, CuotaPlan, PlanDePago


@receiver(post_save, sender=LiquidacionPeriodo)
def recalcular_totales_liquidacion(sender, instance, **kwargs):
    """
    Cada vez que se guarda un período, recalcula los totales de la liquidación cabecera.
    """
    instance.liquidacion.recalcular_totales()


@receiver(post_save, sender=CuotaPlan)
def verificar_plan_cumplido(sender, instance, **kwargs):
    """
    Si todas las cuotas del plan están pagadas, marca el plan como cumplido
    y la liquidación como pagada.
    """
    plan = instance.plan
    todas_pagadas = not plan.cuotas.filter(pagado=False).exists()
    if todas_pagadas and plan.estado != "cumplido":
        plan.estado = "cumplido"
        plan.save(update_fields=["estado"])
        plan.liquidacion.estado = "pagada"
        plan.liquidacion.save(update_fields=["estado"])
