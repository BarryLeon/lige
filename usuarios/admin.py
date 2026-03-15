from django.contrib import admin
from .models import PerfilUsuario


@admin.register(PerfilUsuario)
class PerfilUsuario(admin.ModelAdmin):
    list_display = ('apellido', 'nombre',)
    search_fields = ('user__username', 'apellido', 'nombre',)
    ordering = ('-apellido',)




