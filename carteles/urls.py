from django.urls import path
from . import views

urlpatterns = [
    path("", views.lista_carteles, name="carteles_lista"),
    path("<int:pk>/", views.detalle_cartel, name="carteles_detalle"),
    path("importar/", views.importar_desde_kobo, name="carteles_importar"),
    path("<int:pk>/reprocesar/", views.reprocesar_cartel, name="carteles_reprocesar"),
    path("<int:pk>/descartar/", views.descartar_cartel, name="carteles_descartar"),
    path("<int:pk>/restaurar/", views.restaurar_cartel, name="carteles_restaurar"),
]