from django.urls import path
from .views import (
    home_view,
    subir_archivo_view,
    procesar_archivo_view,
    eliminar_archivo_view,
    revertir_procesamiento_view,
    generar_liquidacion_view,
    descargar_liquidacion_view,
    consultas_view,
    estadisticas_view,
)

urlpatterns = [
    path('',                                    home_view,                   name='home'),
    path('subir/',                              subir_archivo_view,          name='subir_archivo'),
    path('procesar/<int:archivo_id>/',          procesar_archivo_view,       name='procesar_archivo'),
    path('eliminar/<int:archivo_id>/',          eliminar_archivo_view,       name='eliminar_archivo'),
    path('revertir/<int:archivo_id>/',          revertir_procesamiento_view, name='revertir_procesamiento'),
    path('liquidar/<int:archivo_id>/',          generar_liquidacion_view,    name='generar_liquidacion'),
    path('descargar/<int:liquidacion_id>/',     descargar_liquidacion_view,  name='descargar_liquidacion'),
    path('consultas/',                          consultas_view,              name='consultas'),
    path('estadisticas/',                       estadisticas_view,           name='estadisticas'),
]