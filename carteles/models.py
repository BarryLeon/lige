from django.db import models


# ════════════════════════════════════════════════════════════════════════════
# PERSONAS Y PARCELAS
# ════════════════════════════════════════════════════════════════════════════

class Persona(models.Model):
    """
    Persona física o jurídica.
    - Propietario de terreno: vinculada a Parcela
    - Propietario de cartel: vinculada a Cartel
    Nunca es las dos cosas a la vez (regla de negocio).
    """
    TIPO_CHOICES = [
        ("fisica",   "Persona física"),
        ("juridica", "Persona jurídica"),
    ]
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default="fisica")

    apellido     = models.CharField(max_length=200, blank=True, null=True)
    nombre       = models.CharField(max_length=200, blank=True, null=True)
    razon_social = models.CharField(
        max_length=300, blank=True, null=True,
        help_text="Solo para personas jurídicas",
    )
    cuit_dni = models.CharField(
        max_length=20, unique=True,
        help_text="CUIT para personas jurídicas, DNI para físicas",
    )
    domicilio  = models.CharField(max_length=300, blank=True, null=True)
    telefono   = models.CharField(max_length=50,  blank=True, null=True)
    email      = models.EmailField(blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)

    creado      = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Persona"
        verbose_name_plural = "Personas"
        ordering = ["apellido", "razon_social"]

    def __str__(self):
        return f"{self.nombre_completo()} ({self.cuit_dni})"

    def nombre_completo(self):
        if self.tipo == "juridica":
            return self.razon_social or "—"
        partes = [p for p in [self.apellido, self.nombre] if p]
        return " ".join(partes) if partes else "—"


class Parcela(models.Model):
    """
    Terreno donde están instalados los carteles.
    Un propietario de terreno, pero puede tener varios carteles
    de distintos propietarios.
    """
    circunscripcion = models.CharField(max_length=20, blank=True, null=True)
    seccion         = models.CharField(max_length=20, blank=True, null=True)
    chacra          = models.CharField(max_length=20, blank=True, null=True)
    parcela_nro     = models.CharField(
        max_length=20, blank=True, null=True,
        verbose_name="Parcela",
    )
    direccion     = models.CharField(max_length=300, blank=True, null=True)
    localidad     = models.CharField(max_length=100, blank=True, null=True)
    observaciones = models.TextField(blank=True, null=True)

    propietario_terreno = models.ForeignKey(
        Persona,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="parcelas_propias",
        help_text="Propietario del terreno (distinto al dueño del cartel)",
    )

    creado      = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Parcela"
        verbose_name_plural = "Parcelas"
        ordering = ["circunscripcion", "seccion", "parcela_nro"]

    def __str__(self):
        return f"{self.nomenclatura()} — {self.direccion or 'Sin dirección'}"

    def nomenclatura(self):
        partes = []
        if self.circunscripcion: partes.append(f"Circ.{self.circunscripcion}")
        if self.seccion:         partes.append(f"Sec.{self.seccion}")
        if self.chacra:          partes.append(f"Ch.{self.chacra}")
        if self.parcela_nro:     partes.append(f"Parc.{self.parcela_nro}")
        return " ".join(partes) if partes else "Sin nomenclatura"


# ════════════════════════════════════════════════════════════════════════════
# CARTEL
# ════════════════════════════════════════════════════════════════════════════

class Cartel(models.Model):

    # ── Relaciones de gestión ────────────────────────────────────────────────
    parcela = models.ForeignKey(
        Parcela,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="carteles",
        help_text="Parcela donde está instalado el cartel",
    )
    propietario_cartel = models.ForeignKey(
        Persona,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="carteles_propios",
        help_text="Propietario del cartel (distinto al dueño del terreno)",
    )

    # ── Campos que vienen de KoboCollect ────────────────────────────────────
    kobo_id = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text="ID único del registro en KoboToolbox (_id)",
    )
    foto = models.ImageField(upload_to="carteles/fotos/", null=True, blank=True)
    tipo_foto = models.CharField(max_length=100, blank=True, null=True)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    tipo_ubicacion_gps = models.CharField(max_length=50, blank=True, null=True)
    distancia = models.FloatField(
        null=True, blank=True,
        help_text="Distancia en metros entre el operador y el cartel",
    )

    TIPO_CARTEL_CHOICES = [
        ("publicitario", "Publicitario"),
        ("politico",     "Político"),
        ("senalizacion", "Señalización"),
        ("informativo",  "Informativo"),
        ("otro",         "Otro"),
    ]
    tipo_cartel = models.CharField(
        max_length=50, choices=TIPO_CARTEL_CHOICES, blank=True, null=True,
    )

    ESTADO_CHOICES = [
        ("bueno",   "Bueno"),
        ("regular", "Regular"),
        ("malo",    "Malo"),
    ]
    estado_cartel = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, blank=True, null=True,
    )

    observaciones = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(
        null=True, blank=True,
        help_text="Fecha de captura del formulario",
    )
    operador = models.CharField(max_length=200, blank=True, null=True)

    # ── Campos calculados por el detector ───────────────────────────────────
    cartel_detectado    = models.BooleanField(null=True, blank=True)
    confianza_deteccion = models.FloatField(null=True, blank=True)
    bbox_x = models.IntegerField(null=True, blank=True)
    bbox_y = models.IntegerField(null=True, blank=True)
    bbox_w = models.IntegerField(null=True, blank=True)
    bbox_h = models.IntegerField(null=True, blank=True)
    ancho_m      = models.FloatField(null=True, blank=True)
    alto_m       = models.FloatField(null=True, blank=True)
    superficie_m2 = models.FloatField(null=True, blank=True)
    metodo_superficie = models.CharField(max_length=20, blank=True, null=True)
    manual_esquinas = models.JSONField(blank=True, null=True)
    origen_medicion = models.CharField(max_length=20, blank=True, null=True)
    diagnostico_geometria_inconsistente = models.BooleanField(default=False)
    detalle_diagnostico = models.TextField(blank=True, null=True)
    foto_anotada = models.ImageField(
        upload_to="carteles/anotadas/", null=True, blank=True,
    )

    # ── OCR ──────────────────────────────────────────────────────────────────
    texto_ocr            = models.TextField(blank=True, null=True)
    advertencia_sin_texto = models.BooleanField(default=False)

    # ── Estado del procesamiento ─────────────────────────────────────────────
    ESTADO_PROC_CHOICES = [
        ("pendiente", "Pendiente"),
        ("ok",        "Procesado OK"),
        ("error",     "Con errores"),
    ]
    estado_procesamiento = models.CharField(
        max_length=20, choices=ESTADO_PROC_CHOICES, default="pendiente",
    )

    # ── Estado del registro ──────────────────────────────────────────────────
    ESTADO_REGISTRO_CHOICES = [
        ("activo",      "Activo"),
        ("descartado",  "Descartado"),
    ]
    estado_registro = models.CharField(
        max_length=20, choices=ESTADO_REGISTRO_CHOICES, default="activo",
    )
    motivo_descarte = models.TextField(blank=True, null=True)
    descartado_por  = models.ForeignKey(
        "auth.User", null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="carteles_descartados",
        db_constraint=False,  # Evita error de FK a auth_user si se borra el usuario
    )
    descartado_en = models.DateTimeField(null=True, blank=True)

    # ── Errores individualizados ─────────────────────────────────────────────
    error_sin_foto           = models.BooleanField(default=False)
    error_sin_deteccion      = models.BooleanField(default=False)
    error_distancia_invalida = models.BooleanField(default=False)
    error_imagen_ilegible    = models.BooleanField(default=False)
    error_gps_invalido       = models.BooleanField(default=False)
    error_zoom_sospechoso    = models.BooleanField(default=False)
    detalle_error            = models.TextField(blank=True, null=True)

    creado      = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cartel"
        verbose_name_plural = "Carteles"
        ordering = ["-creado"]

    def __str__(self):
        return f"Cartel #{self.id} — {self.get_tipo_cartel_display() or 'Sin tipo'} ({self.estado_procesamiento})"

    def es_descartado(self):
        return self.estado_registro == "descartado"

    def tiene_errores(self):
        return any([
            self.error_sin_foto, self.error_sin_deteccion,
            self.error_distancia_invalida, self.error_imagen_ilegible,
            self.error_gps_invalido, self.error_zoom_sospechoso,
        ])

    def lista_errores(self):
        errores = []
        if self.error_sin_foto:
            errores.append("El formulario no tenía foto adjunta. Por favor, volvé a registrar el cartel con foto.")
        if self.error_imagen_ilegible:
            errores.append("La imagen no pudo leerse. Puede estar corrupta o en un formato no soportado.")
        if self.error_gps_invalido:
            errores.append("Las coordenadas GPS son inválidas. Verificá que el GPS estuviera activo al momento de la captura.")
        if self.error_distancia_invalida:
            errores.append("La distancia al cartel es inválida (nula o cero). Completá ese campo correctamente.")
        if self.error_sin_deteccion:
            errores.append("No se detectó ningún cartel en la foto. Intentá sacar la foto más de frente y con mejor iluminación.")
        if self.error_zoom_sospechoso:
            errores.append("La superficie calculada parece incorrecta: posiblemente se usó zoom o la distancia ingresada no es correcta. Retomá la foto SIN zoom e ingresá la distancia real al cartel.")
        return errores

    def lista_advertencias(self):
        advertencias = []
        if self.advertencia_sin_texto:
            advertencias.append("No se encontró texto legible en el cartel. Puede ser un cartel solo con imágenes o la foto no tiene suficiente resolución.")
        if self.diagnostico_geometria_inconsistente and self.detalle_diagnostico:
            advertencias.append(self.detalle_diagnostico)
        return advertencias

    def publicidad_actual(self):
        return self.historial_publicidad.filter(fecha_hasta__isnull=True).first()

    def propietario_actual(self):
        return self.propietario_cartel

    def tiene_esquinas_manuales(self):
        return bool(self.manual_esquinas and len(self.manual_esquinas) == 4)


# ════════════════════════════════════════════════════════════════════════════
# HISTORIAL DE PUBLICIDAD
# ════════════════════════════════════════════════════════════════════════════

class HistorialPublicidad(models.Model):
    """
    Empresa/persona que publicita en el cartel en un período determinado.
    Solo una activa a la vez por cartel (fecha_hasta=None es la actual).
    La empresa publicista debe estar registrada como Persona en el sistema.
    """
    cartel = models.ForeignKey(
        Cartel, on_delete=models.CASCADE,
        related_name="historial_publicidad",
    )
    empresa = models.ForeignKey(
        Persona,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="publicidades",
        help_text="Empresa o persona que publicita en el cartel",
    )
    fecha_desde = models.DateField(null=True, blank=True)
    fecha_hasta = models.DateField(
        null=True, blank=True,
        help_text="Vacío = publicita actualmente",
    )
    observaciones = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Publicidad"
        verbose_name_plural = "Historial de publicidades"
        ordering = ["-fecha_desde"]

    def __str__(self):
        estado = "actual" if not self.fecha_hasta else str(self.fecha_hasta)
        nombre = self.empresa.nombre_completo() if self.empresa else "Sin empresa"
        return f"{nombre} — Cartel #{self.cartel_id} (hasta {estado})"

    def es_actual(self):
        return self.fecha_hasta is None

    def nombre_empresa(self):
        """Devuelve el nombre de la empresa para usar en templates."""
        return self.empresa.nombre_completo() if self.empresa else "—"
