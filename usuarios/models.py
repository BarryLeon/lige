from django.db import models
from django.contrib.auth.models import User


class PerfilUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    
    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfil de Usuarios"

    nombre = models.CharField(max_length=100, )
    apellido = models.CharField(max_length=100, )
    fecha_nacimiento = models.DateField(null=True, blank=True, )
    dni = models.PositiveBigIntegerField(null=True, blank=True, )
    sexo = models.CharField(max_length=1, choices=[
        ('M', 'Masculino'),
        ('F', 'Femenino'),
        ('O', 'Otro'),
    ], default='O', )

    celu = models.CharField(max_length=15, blank=True, null=True, )
