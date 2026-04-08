from django.apps import AppConfig


class TasacartelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasacartel"
    verbose_name = "Tasa Cartel"

    def ready(self):
        import tasacartel.signals  # noqa: F401 — registra los signals
