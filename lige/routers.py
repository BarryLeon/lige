# lige/routers.py

MARCHIQUITA_APPS = {"tasas_marchiquita"}
CARTELES_APPS    = {"carteles", "tasacartel"}


class ClientesRouter:

    def _get_db(self, app_label):
        if app_label in MARCHIQUITA_APPS:
            return "marchiquita"
        if app_label in CARTELES_APPS:
            return "carteles"
        return "default"

    def db_for_read(self, model, **hints):
        return self._get_db(model._meta.app_label)

    def db_for_write(self, model, **hints):
        return self._get_db(model._meta.app_label)

    def allow_relation(self, obj1, obj2, **hints):
        # Solo permite relaciones entre modelos de la misma base
        return self._get_db(obj1._meta.app_label) == self._get_db(obj2._meta.app_label)

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == self._get_db(app_label)