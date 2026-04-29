# AGENTS.md

Este proyecto en Django es una plataforma integral para la gestión de carteles publicitarios y tasas municipales, con múltiples módulos interconectados y una arquitectura de bases de datos multi-tenant. A continuación, se detallan los agentes que conforman el sistema, adaptados al contexto real del proyecto:

1. Agente de autenticación
2. Agente de autorización
3. Agente de gestión de bases de datos
4. Agente de gestión de archivos
5. Agente de seguridad
6. Agente de monitoreo
7. Agente de registro
8. Agente de notificaciones
9. Agente de integración con terceros
10. Agente de pruebas

# Agente de autenticación

El agente de autenticación gestiona el acceso del usuario al sistema mediante el middleware `LoginRequiredMiddleware` en `principal/middleware.py`, que redirige a la página de login (`usuarios/views.py`) si el usuario no está autenticado. El sistema utiliza Django's built-in `auth.User` y el modelo `PerfilUsuario` en `usuarios/models.py` para extender la información del usuario. La autenticación se realiza a través de un formulario personalizado (`CustomAuthenticationForm`), y la sesión se configura con expiración de 30 minutos (`SESSION_COOKIE_AGE = 1800`) y expira al cerrar el navegador (`SESSION_EXPIRE_AT_BROWSER_CLOSE = True`).

# Agente de autorización

El agente de autorización controla los permisos de acceso a funcionalidades específicas según el rol del usuario. Aunque no se implementa un sistema de roles explícito, la autorización se maneja a través de la lógica de negocio en las vistas y servicios. Por ejemplo, solo los usuarios autorizados pueden generar liquidaciones, facturas o modificar datos sensibles. El acceso a ciertas vistas está restringido mediante el decorador `@login_required`. La integración con el sistema de autenticación de Django permite definir permisos a nivel de modelo y vista.

# Agente de gestión de bases de datos

El agente de gestión de bases de datos administra una arquitectura multi-database con 4 bases de datos distintas, definidas en `lige/settings.py` y configuradas mediante un router personalizado (`lige/routers.py`). Cada base de datos está asignada a un conjunto específico de aplicaciones:

- `default`: Contiene `auth`, `sessions`, `admin`, `principal`, `usuarios`.
- `marchiquita`: Gestiona la app `tasas_marchiquita` (gestión de deudas y cuotas de contribuyentes).
- `carteles`: Maneja `carteles` y `tasacartel` (registro de carteles, medición, tasas y liquidaciones).
- `cobros_publivial`: Administra `cobros_publivial` (honorarios, facturación y cobros).

El router `ClientesRouter` dirige las consultas de lectura y escritura según el `app_label` del modelo, asegurando que las operaciones se realicen en la base de datos correcta. Las relaciones entre modelos de distintas bases de datos se manejan con `db_constraint=False` para evitar restricciones de clave foránea cruzada.

# Agente de gestión de archivos

El agente de gestión de archivos maneja la carga, almacenamiento y procesamiento de archivos en el sistema. Se utilizan los siguientes tipos de archivos:

- **Imágenes de carteles**: Almacenadas en `media/carteles/fotos/` y `media/carteles/anotadas/` (vía `ImageField` en `carteles/models.py`).
- **Archivos Excel de importación**: Subidos en `tasas_marchiquita` y almacenados como `BinaryField` en `ArchivoImportacion`.
- **PDFs de liquidación**: Generados y almacenados como `BinaryField` en `Liquidacion`.
- **Archivos de configuración**: `.env`, `.env.prod` para variables de entorno.

Los archivos se sirven a través de `MEDIA_URL = '/media/'` y `MEDIA_ROOT = os.path.join(BASE_DIR, 'media')`. El sistema permite la subida de archivos de hasta 10 MB (`DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024`).

# Agente de seguridad

El agente de seguridad protege el sistema contra amenazas y vulnerabilidades. El proyecto implementa:

- **Protección CSRF**: Habilitada por defecto en `MIDDLEWARE`.
- **Seguridad de sesión**: Sesiones expiran al cerrar navegador y tras 30 minutos de inactividad.
- **Validación de entradas**: Uso de `full_clean()` y validadores en modelos (por ejemplo, `MinValueValidator`, `MaxValueValidator`).
- **Protección contra XSS/SQLi**: Uso de ORM de Django y escape automático en templates.
- **Control de acceso**: Middleware `LoginRequiredMiddleware` para proteger rutas.
- **Manejo seguro de secretos**: Clave secreta cargada desde `.env` con `environ`.
- **Protección de archivos**: No se permiten subidas de archivos ejecutables, solo imágenes y Excel.

# Agente de monitoreo

El agente de monitoreo no está implementado como un componente independiente, pero se logra a través de:

- **Logs de acceso**: Django registra automáticamente las solicitudes HTTP.
- **Estadísticas en vistas**: La app `tasas_marchiquita` tiene una vista de estadísticas (`estadisticas_view`) que muestra totales de deudas, parcelas, etc.
- **Registro de eventos**: Cada operación crítica (importación, generación de liquidación, facturación) registra el usuario y la fecha de acción.
- **Estado de procesamiento**: Modelos como `Cartel` y `ArchivoImportacion` tienen campos de estado (`estado_procesamiento`, `procesado`) que permiten monitorear el flujo de trabajo.

# Agente de registro

El agente de registro captura y almacena eventos y acciones realizadas en el sistema. Se implementa mediante:

- **Campos de auditoría**: Todos los modelos principales incluyen `creado` y `actualizado` (`auto_now_add`, `auto_now`).
- **Registro de usuario**: Campos como `generado_por_id_val` en `Liquidacion` y `creado_por` en `Factura` guardan el ID del usuario que realizó la acción.
- **Historial de versiones**: En `tasacartel`, las liquidaciones generan nuevas versiones (`version`) cuando se editan, conservando el historial de cambios.
- **Logs de importación**: `ArchivoImportacion` registra cuántos registros se importaron, cuándo y por quién.

# Agente de notificaciones

El agente de notificaciones envía alertas y mensajes al usuario a través de Django's message framework (`django.contrib.messages`). Las notificaciones se muestran en las interfaces web y se utilizan para:

- Confirmar operaciones exitosas ("Liquidación creada correctamente").
- Informar errores ("La imagen está corrupta").
- Alertar sobre advertencias ("No se detectó ningún cartel").

No se implementan notificaciones por correo electrónico ni push, pero el sistema está preparado para integrarse con servicios externos.

# Agente de integración con terceros

El agente de integración con terceros se encarga de conectar el sistema con plataformas externas:

- **KoboToolbox**: La app `carteles` importa datos de formularios de Kobo (`importar_kobo.py`) y permite borrar submissions (`kobo_delete.py`).
- **OCR**: El servicio `ocr_cartel.py` utiliza OCR para extraer texto de las imágenes de carteles.
- **Detector de carteles**: Usa modelos de machine learning (`cartel_detector.py`, `torch_utils.py`) para detectar y medir carteles en imágenes.
- **Exportación a Excel/PDF**: Genera informes y reportes exportables para uso externo.

# Agente de pruebas

El agente de pruebas realiza pruebas unitarias y de integración en las aplicaciones del proyecto. Cada app tiene un archivo `tests.py`:

- `carteles/tests.py`
- `cobros_publivial/tests.py`
- `tasacartel/tests.py`
- `tasas_marchiquita/tests.py`

Estas pruebas validan el comportamiento de modelos, vistas y servicios. El proyecto también incluye scripts de automatización (`script_actualizar_bases`, `scripts/`) que pueden ser usados en procesos CI/CD para validar el estado del sistema antes de despliegues.