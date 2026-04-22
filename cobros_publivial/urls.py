from django.urls import path
from . import views

app_name = 'cobros_publivial'

urlpatterns = [
    # Dashboard general
    path('', views.dashboard_view, name='dashboard'),

    # Facturas
    path('facturas/', views.facturas_lista_view, name='facturas_lista'),
    path('facturas/<int:factura_id>/', views.factura_detalle_view, name='factura_detalle'),
    path('facturas/<int:factura_id>/cobrada/', views.factura_cobrada_view, name='factura_cobrada'),
    path('facturas/<int:factura_id>/anular/', views.factura_anular_view, name='factura_anular'),

    # Tasacartel — honorarios y cuotas
    path('cartel/honorarios/', views.cartel_honorarios_view, name='cartel_honorarios'),
    path('cartel/contado/', views.cartel_contado_view, name='cartel_contado'),
    path('cartel/cuotas/', views.cartel_cuotas_view, name='cartel_cuotas'),

    # Marchiquita — honorarios (auditoría)
    path('marchiquita/honorarios/', views.marchiquita_honorarios_view, name='marchiquita_honorarios'),
]
