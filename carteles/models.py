from django.db import models


class Cartel(models.Model):

    # ── Campos que vienen de KoboCollect ────────────────────────────────────
    kobo_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="ID único del registro en KoboToolbox (_id)",
    )

    foto = models.ImageField(
        upload_to="carteles/fotos/",
        null=True,
        blank=True,
    )

    tipo_foto = models.CharField(max_length=100, blank=True, null=True)

    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    tipo_ubicacion_gps = models.CharField(max_length=50, blank=True, null=True)

    distancia = models.FloatField(
        null=True,
        blank=True,
        help_text="Distancia en metros entre el operador y el cartel",
    )

    TIPO_CARTEL_CHOICES = [
        ("publicitario", "Publicitario"),
        ("politico", "Político"),
        ("senalizacion", "Señalización"),
        ("informativo", "Informativo"),
        ("otro", "Otro"),
    ]
    tipo_cartel = models.CharField(
        max_length=50,
        choices=TIPO_CARTEL_CHOICES,
        blank=True,
        null=True,
    )

    ESTADO_CHOICES = [
        ("bueno", "Bueno"),
        ("regular", "Regular"),
        ("malo", "Malo"),
    ]
    estado_cartel = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        blank=True,
        null=True,
    )

    observaciones = models.TextField(blank=True, null=True)

    fecha = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha de captura del formulario",
    )

    operador = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Usuario que hizo la captura",
    )

    # ── Campos calculados por el detector ───────────────────────────────────
    cartel_detectado = models.BooleanField(
        null=True,
        blank=True,
        help_text="True si YOLO encontró un cartel, False si no, None si no fue procesado",
    )
    confianza_deteccion = models.FloatField(
        null=True,
        blank=True,
        help_text="Score de confianza de YOLO (0-1)",
    )

    bbox_x = models.IntegerField(null=True, blank=True)
    bbox_y = models.IntegerField(null=True, blank=True)
    bbox_w = models.IntegerField(null=True, blank=True)
    bbox_h = models.IntegerField(null=True, blank=True)

    ancho_m = models.FloatField(null=True, blank=True)
    alto_m = models.FloatField(null=True, blank=True)
    superficie_m2 = models.FloatField(null=True, blank=True)

    foto_anotada = models.ImageField(
        upload_to="carteles/anotadas/",
        null=True,
        blank=True,
        help_text="Foto con el bounding box dibujado",
    )

    # ── Estado del procesamiento ─────────────────────────────────────────────
    ESTADO_PROC_CHOICES = [
        ("pendiente", "Pendiente"),
        ("ok", "Procesado OK"),
        ("error", "Con errores"),
    ]
    estado_procesamiento = models.CharField(
        max_length=20,
        choices=ESTADO_PROC_CHOICES,
        default="pendiente",
    )

    # ── Estado del registro (activo / descartado) ───────────────────────────
    ESTADO_REGISTRO_CHOICES = [
        ("activo", "Activo"),
        ("descartado", "Descartado"),
    ]
    estado_registro = models.CharField(
        max_length=20,
        choices=ESTADO_REGISTRO_CHOICES,
        default="activo",
        help_text="Descartado: no aparece en listados ni reportes",
    )
    motivo_descarte = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo por el cual se descartó el registro",
    )
    descartado_por = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="carteles_descartados",
        help_text="Usuario que descartó el registro",
    )
    descartado_en = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha y hora del descarte",
    )

    # Errores individualizados: cada tipo de error tiene su propio campo
    # para poder filtrar y notificar de forma específica.
    error_sin_foto = models.BooleanField(
        default=False,
        help_text="El formulario fue importado sin foto adjunta",
    )
    error_sin_deteccion = models.BooleanField(
        default=False,
        help_text="YOLO no encontró ningún cartel en la imagen",
    )
    error_distancia_invalida = models.BooleanField(
        default=False,
        help_text="La distancia registrada es nula, cero o negativa",
    )
    error_imagen_ilegible = models.BooleanField(
        default=False,
        help_text="La imagen no pudo ser abierta o está corrupta",
    )
    error_gps_invalido = models.BooleanField(
        default=False,
        help_text="Las coordenadas GPS son nulas o inválidas",
    )
    error_zoom_sospechoso = models.BooleanField(
        default=False,
        help_text="El cálculo sugiere que se usó zoom o la distancia es incorrecta",
    )
    detalle_error = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción técnica del error ocurrido",
    )

    # ── OCR ──────────────────────────────────────────────────────────────────
    texto_ocr = models.TextField(
        blank=True,
        null=True,
        help_text="Texto extraído del cartel por OCR (EasyOCR)",
    )
    advertencia_sin_texto = models.BooleanField(
        default=False,
        help_text="El OCR no encontró texto legible en el cartel",
    )

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cartel"
        verbose_name_plural = "Carteles"
        ordering = ["-creado"]

    def es_descartado(self):
        return self.estado_registro == "descartado"

    def __str__(self):
        return f"Cartel #{self.id} — {self.get_tipo_cartel_display() or 'Sin tipo'} ({self.estado_procesamiento})"

    def tiene_errores(self):
        return any([
            self.error_sin_foto,
            self.error_sin_deteccion,
            self.error_distancia_invalida,
            self.error_imagen_ilegible,
            self.error_gps_invalido,
            self.error_zoom_sospechoso,
        ])

    def lista_errores(self):
        """Devuelve lista de strings con los errores activos, legible para el usuario."""
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
        """Devuelve advertencias no bloqueantes (no impiden el procesamiento)."""
        advertencias = []
        if self.advertencia_sin_texto:
            advertencias.append("No se encontró texto legible en el cartel. Puede ser un cartel solo con imágenes o la foto no tiene suficiente resolución.")
        return advertencias