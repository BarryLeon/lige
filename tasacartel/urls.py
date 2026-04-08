from django.urls import path
from . import views

app_name = "tasacartel"

urlpatterns = [

    # ── API interna ──────────────────────────────────────────────────────────
    path("api/cartel/<int:cartel_id>/", views.api_cartel_datos, name="api_cartel_datos"),

    # ── Valores de tasa anual ────────────────────────────────────────────────
    path("tasas/",                        views.valores_tasa_lista,          name="valores_tasa_lista"),
    path("tasas/nuevo/",                  views.valor_tasa_crear,            name="valor_tasa_crear"),
    path("tasas/<int:pk>/editar/",        views.valor_tasa_editar,           name="valor_tasa_editar"),
    path("tasas/<int:pk>/eliminar/",      views.valor_tasa_eliminar,         name="valor_tasa_eliminar"),

    # ── Liquidaciones ────────────────────────────────────────────────────────
    path("liquidaciones/",                views.liquidaciones_lista,         name="liquidaciones_lista"),
    path("liquidaciones/nueva/",          views.liquidacion_crear,           name="liquidacion_crear"),
    path("liquidaciones/<int:pk>/",       views.liquidacion_detalle,         name="liquidacion_detalle"),
    path("liquidaciones/<int:pk>/editar/",views.liquidacion_editar,          name="liquidacion_editar"),
    path("liquidaciones/<int:pk>/estado/",views.liquidacion_cambiar_estado,  name="liquidacion_cambiar_estado"),

    # ── Historial por contribuyente ──────────────────────────────────────────
    path("contribuyente/<int:persona_id>/historial/", views.historial_contribuyente, name="historial_contribuyente"),

    # ── Planes de pago ───────────────────────────────────────────────────────
    path("liquidaciones/<int:liquidacion_pk>/plan/nuevo/", views.plan_crear,   name="plan_crear"),
    path("planes/<int:pk>/",                               views.plan_detalle, name="plan_detalle"),
    path("cuotas/<int:cuota_pk>/pagar/",                   views.cuota_marcar_pagada, name="cuota_marcar_pagada"),
]