# AGENTS.md — Proyecto LIGE (Sistema Municipal de Carteles y Tasas)

## Descripción del proyecto

Sistema web Django para la Municipalidad de Mar Chiquita (Buenos Aires, Argentina).
Gestiona dos dominios principales:

1. **Carteles publicitarios**: relevamiento de campo vía KoboToolbox, detección
   automática con YOLO, OCR con EasyOCR, liquidación de tasas municipales y
   exportación a Excel/PDF.
2. **Tasas Marchiquita**: importación mensual de padrones de deuda desde Excel,
   planes de cuotas, consultas por deudor/parcela y liquidación de comisiones (25%).

Stack: Django 6.0, Python 3.12, SQLite (dev) / PostgreSQL (prod), Bootstrap 5.3,
YOLO v8 (ultralytics), EasyOCR, OpenCV, openpyxl, ReportLab.

---

## Estructura de apps

```
lige/                   # Proyecto Django (settings, urls raíz)
principal/              # Panel principal, login, middleware de autenticación
usuarios/               # Modelo PerfilUsuario, autenticación
carteles/               # App principal de carteles
  models.py             # Persona, Parcela, Cartel, HistorialPublicidad
  views.py              # CRUD carteles, importar Kobo, reprocesar, informes, exportar
  urls.py               # Rutas del módulo carteles/
  servicios/
    cartel_detector.py  # Detección YOLO → GrabCut → Contornos + cálculo de superficie
    ocr_cartel.py       # EasyOCR sobre el bbox detectado
    importar_kobo.py    # Importación desde KoboToolbox API
    kobo_delete.py      # Borrado de submissions en Kobo
    exportar.py         # Exportación Excel (openpyxl) y PDF (ReportLab)
tasacartel/             # Liquidación de tasa anual por cartel (ordenanza municipal)
  models.py             # ValorTasaAnual, Liquidacion, LiquidacionPeriodo, PlanDePago, CuotaPlan
tasas_marchiquita/      # Gestión de padrones de deuda municipales
  models.py             # ArchivoImportacion, ResponsablePago, Parcela, Deuda, Cuota, Liquidacion
  services.py           # Toda la lógica: guardar/procesar/revertir archivo, consultas, liquidación PDF
templates/              # Templates HTML globales y por app
static_dev/             # CSS (Bootstrap + SCSS custom), JS, imágenes
```

---

## Comandos de desarrollo

```bash
# Activar entorno virtual (ajustar ruta si es distinta)
source venv/bin/activate

# Servidor de desarrollo
python manage.py runserver

# Migraciones
python manage.py makemigrations
python manage.py migrate

# Shell Django
python manage.py shell

# Crear superusuario
python manage.py createsuperuser

# Recolectar archivos estáticos
python manage.py collectstatic --noinput

# Tests
python manage.py test                          # todos los tests
python manage.py test carteles                 # solo app carteles
python manage.py test tasas_marchiquita        # solo app tasas_marchiquita
python manage.py test carteles.tests.NombreTestCase  # un test específico
```

---

## Variables de entorno (.env)

El proyecto usa `django-environ`. El archivo `.env` debe estar en la raíz.
Variables mínimas requeridas:

```
SECRET_KEY=...
DEBUG=True
DATABASE_URL=sqlite:///db.sqlite3
# o para PostgreSQL:
# DATABASE_URL=postgres://user:pass@localhost:5432/dbname
```

---

## Convenciones de código

### General
- Python 3.12. Sin type hints obligatorios, pero se usan en funciones de servicio
  (`services.py`, `cartel_detector.py`).
- Nombres en **español** para modelos, campos, variables de negocio y mensajes al
  usuario. Nombres en inglés para patrones Django estándar (`request`, `queryset`,
  `pk`, etc.).
- Strings de mensajes al usuario siempre en español (usan `messages.success/error/warning`).
- `f-strings` para interpolación. Evitar `%` y `.format()`.

### Modelos
- Cada modelo tiene `creado` y `actualizado` con `auto_now_add` / `auto_now`.
- Campos opcionales usan `null=True, blank=True`. Campos requeridos no llevan default.
- `__str__` siempre definido y descriptivo.
- `class Meta` con `verbose_name`, `verbose_name_plural` y `ordering`.
- ForeignKeys con `on_delete` explícito y `related_name` descriptivo.
- Choices como lista de tuplas en el modelo, nombradas `NOMBRE_CHOICES`.

### Vistas
- Vistas basadas en funciones (FBV), no CBV.
- Protección de métodos: `if request.method != "POST": return redirect(...)`.
- No hay Django Forms: los formularios HTML hacen POST directo y las vistas
  leen `request.POST` manualmente, llaman `full_clean()` antes de `save()`.
- Siempre usar `get_object_or_404` para obtener objetos por PK.
- Mensajes de feedback con `django.contrib.messages`.
- Redirección post-POST con `redirect('nombre_url')`.

### URLs
- Nombres de URL con prefijo de app: `carteles_lista`, `carteles_detalle`,
  `tasas_marchiquita_panel`, etc.
- PKs en URLs como `<int:pk>`.

### Servicios / lógica de negocio
- Toda la lógica pesada va en `servicios/` (carteles) o `services.py`
  (tasas_marchiquita), nunca en vistas.
- Funciones de servicio retornan dicts con claves bien definidas.
- Errores de negocio se propagan como `ValueError` con mensaje descriptivo.
- Operaciones que tocan múltiples tablas usan `@transaction.atomic`.
- Las funciones de servicio públicas tienen docstring explicando parámetros
  y valor de retorno.

### Templates
- Base: `base.html` → `layout_interno.html` (páginas autenticadas) /
  `layout_login.html` (login).
- Bootstrap 5.3 para estilos. SCSS custom en `static_dev/scss/`.
- Variables de contexto en snake_case español: `carteles`, `totales`, `stats`.

---

## Modelos clave — resumen rápido

### App `carteles`
- `Persona` — física o jurídica, identificada por `cuit_dni` (unique).
- `Parcela` — terreno con nomenclatura catastral. FK a `Persona` (propietario terreno).
- `Cartel` — registro de campo. FK a `Parcela` y `Persona` (propietario cartel).
  Estados de procesamiento: `pendiente` / `ok` / `error`.
  Estados de registro: `activo` / `descartado`.
  Campos calculados: `bbox_*`, `ancho_m`, `alto_m`, `superficie_m2`, `texto_ocr`.
- `HistorialPublicidad` — empresa que publicita en el cartel en un período.
  `fecha_hasta=None` significa publicidad actual.

### App `tasacartel`
- `ValorTasaAnual` — valor $/m² por año fiscal (histórico desde 2021).
- `Liquidacion` — cabecera de liquidación de tasa. Responsabilidad solidaria
  entre propietario cartel, propietario terreno y empresa publicista.
- `LiquidacionPeriodo` — detalle año por año. Calcula mora con interés simple
  (4% mensual desde vencimiento 31/12 del año fiscal).
- `PlanDePago` → `CuotaPlan` — plan de pagos sobre liquidación conformada.
  Cuota 1 = anticipo sin interés; cuotas 2..N con interés simple.

### App `tasas_marchiquita`
- `ArchivoImportacion` — Excel binario guardado en BD. Flujo: subir → procesar → (revertir).
- `ResponsablePago` / `Parcela` / `Deuda` / `Cuota` — padrón de deudas.
- `Liquidacion` — comisión del 25% sobre lo cobrado en el período. Incluye PDF generado con ReportLab.

---

## Servicios críticos

### `cartel_detector.py`
Cascada de detección: **YOLO** (modelo `carteles_yolo.pt`, confianza mínima 0.30)
→ **GrabCut** → **Contornos OpenCV**.
Cálculo de superficie por semejanza de triángulos usando FOV derivado del EXIF
(`FocalLengthIn35mmFilm`). Fallback a 65° si no hay EXIF. Tabla `_FOV_POR_MODELO`
para celulares sin ese campo.
Flag `zoom_sospechoso` si ancho calculado < 5cm o > 25m.

### `ocr_cartel.py`
EasyOCR con idiomas `["es", "en"]`. Singleton lazy `_lector`. Recorta el bbox
con margen de 10px y escala a mínimo 300px de alto antes de procesar.
Confianza mínima: 0.25.

### `tasas_marchiquita/services.py`
Dos pasos: `guardar_archivo()` (solo sube) y `procesar_archivo()` (valida todo
antes de escribir — si hay un solo error aborta sin procesar nada).
`revertir_procesamiento()` elimina Deudas y Cuotas pero conserva datos maestros
(Parcelas, ResponsablePago).
Columnas requeridas del Excel definidas en `COLUMNAS_REQUERIDAS`.

---

## Reglas de negocio importantes

- Una `Persona` es propietaria de terreno **o** de cartel, nunca ambas.
- `HistorialPublicidad` con `fecha_hasta=None` es la publicidad activa. Al agregar
  una nueva sin `fecha_hasta`, se cierra la anterior automáticamente.
- La liquidación de tasa (`tasacartel`) usa snapshots de los responsables al momento
  de la determinación para que el historial no cambie si se editan los datos.
- El archivo Excel de `tasas_marchiquita` se valida en dos pasadas: primero todas
  las filas, y solo si no hay errores se escribe en la BD.
- Deuda original = valor del primer archivo de importación de cada parcela.
  Los archivos mensuales solo suman pagos; el `VALOR_TOTAL_DEUDA` debe coincidir
  exactamente con el del archivo inicial.

---

## Archivos que NO modificar sin revisión

- `carteles/servicios/carteles_yolo.pt` — modelo YOLO entrenado.
- `carteles/servicios/modelos_yolo/` — pesos alternativos y métricas.
- `.env` — variables de entorno sensibles.
- `db.sqlite3` — base de datos de desarrollo.
