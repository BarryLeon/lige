from django.urls import path
from . import views

urlpatterns = [
    # ── Carteles ──────────────────────────────────────────────────────────────
    path("", views.lista_carteles, name="carteles_lista"),
    path("<int:pk>/", views.detalle_cartel, name="carteles_detalle"),
    path("importar/", views.importar_desde_kobo, name="carteles_importar"),
    path("<int:pk>/reprocesar/", views.reprocesar_cartel, name="carteles_reprocesar"),
    path("<int:pk>/descartar/", views.descartar_cartel, name="carteles_descartar"),
    path("<int:pk>/restaurar/", views.restaurar_cartel, name="carteles_restaurar"),
    path("<int:pk>/gestion/", views.actualizar_gestion_cartel, name="carteles_gestion"),
    path("<int:pk>/publicidad/agregar/", views.agregar_publicidad, name="carteles_pub_agregar"),
    path("<int:pk>/publicidad/<int:pub_id>/eliminar/", views.eliminar_publicidad, name="carteles_pub_eliminar"),

    # ── Informes ──────────────────────────────────────────────────────────────
    path("informes/", views.informes, name="carteles_informes"),
    path("informes/excel/", views.exportar_excel_view, name="carteles_exportar_excel"),
    path("informes/pdf/",   views.exportar_pdf_view,   name="carteles_exportar_pdf"),

    # ── Personas ──────────────────────────────────────────────────────────────
    path("personas/", views.lista_personas, name="carteles_personas_lista"),
    path("personas/crear/", views.crear_persona, name="carteles_personas_crear"),
    path("personas/<int:pk>/editar/", views.editar_persona, name="carteles_personas_editar"),

    # ── Parcelas ──────────────────────────────────────────────────────────────
    path("parcelas/", views.lista_parcelas, name="carteles_parcelas_lista"),
    path("parcelas/crear/", views.crear_parcela, name="carteles_parcelas_crear"),
    path("parcelas/<int:pk>/editar/", views.editar_parcela, name="carteles_parcelas_editar"),
]