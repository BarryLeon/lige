from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from carteles.models import Cartel, Parcela, Persona
from tasacartel.forms import PlanDePagoForm
from tasacartel.models import Liquidacion, LiquidacionPeriodo, PlanDePago, ValorTasaAnual


class LiquidacionEditarTests(TestCase):
    databases = {"default", "carteles"}

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="secreto123",
        )
        self.client.force_login(self.user)

        self.propietario_cartel = Persona.objects.create(
            tipo="fisica",
            apellido="Perez",
            nombre="Juan",
            cuit_dni="20111222333",
        )
        self.propietario_terreno = Persona.objects.create(
            tipo="fisica",
            apellido="Gomez",
            nombre="Ana",
            cuit_dni="20999888777",
        )
        self.parcela = Parcela.objects.create(
            circunscripcion="I",
            seccion="A",
            parcela_nro="123",
            propietario_terreno=self.propietario_terreno,
        )
        self.cartel = Cartel.objects.create(
            parcela=self.parcela,
            propietario_cartel=self.propietario_cartel,
            kobo_id="kobo-test-1",
            estado_procesamiento="ok",
            estado_registro="activo",
            superficie_m2=10,
        )
        self.valor_tasa = ValorTasaAnual.objects.create(
            anio=2026,
            valor_m2=Decimal("1000.00"),
        )

    def _crear_liquidacion(self, estado="borrador"):
        liquidacion = Liquidacion.objects.create(
            cartel=self.cartel,
            propietario_cartel=self.propietario_cartel,
            propietario_terreno=self.propietario_terreno,
            superficie_m2=Decimal("10.0000"),
            estado=estado,
            version=1,
        )
        LiquidacionPeriodo.objects.create(
            liquidacion=liquidacion,
            valor_tasa=self.valor_tasa,
            anio_fiscal=2026,
            valor_m2_aplicado=self.valor_tasa.valor_m2,
            tasa_mora_mensual=Decimal("0.0400"),
        )
        return liquidacion

    def _post_edicion(self, liquidacion):
        return self.client.post(
            reverse("tasacartel:liquidacion_editar", args=[liquidacion.pk]),
            data={
                "cartel": str(self.cartel.pk),
                "superficie_m2": "12.0000",
                "observaciones": "Ajuste de metros",
                "periodos-TOTAL_FORMS": "1",
                "periodos-INITIAL_FORMS": "0",
                "periodos-MIN_NUM_FORMS": "1",
                "periodos-MAX_NUM_FORMS": "1000",
                "periodos-0-valor_tasa": str(self.valor_tasa.pk),
                "periodos-0-anio_fiscal": "2026",
                "periodos-0-tasa_mora_mensual": "0.0400",
            },
        )

    def test_editar_borrador_mantiene_estado_borrador_en_version_anterior(self):
        original = self._crear_liquidacion(estado="borrador")

        response = self._post_edicion(original)

        self.assertEqual(response.status_code, 302)
        original.refresh_from_db()
        self.assertEqual(original.estado, "borrador")

        nueva = Liquidacion.objects.exclude(pk=original.pk).get()
        self.assertEqual(nueva.estado, "borrador")
        self.assertEqual(nueva.version, 2)

    def test_editar_notificada_marca_objetada_la_version_anterior(self):
        original = self._crear_liquidacion(estado="notificada")

        response = self._post_edicion(original)

        self.assertEqual(response.status_code, 302)
        original.refresh_from_db()
        self.assertEqual(original.estado, "objetada")


class PlanDePagoTests(TestCase):
    databases = {"carteles"}

    def setUp(self):
        self.propietario_cartel = Persona.objects.create(
            tipo="fisica",
            apellido="Lopez",
            nombre="Mario",
            cuit_dni="20333444555",
        )
        self.propietario_terreno = Persona.objects.create(
            tipo="fisica",
            apellido="Diaz",
            nombre="Laura",
            cuit_dni="20777666555",
        )
        self.parcela = Parcela.objects.create(
            circunscripcion="II",
            seccion="B",
            parcela_nro="45",
            propietario_terreno=self.propietario_terreno,
        )
        self.cartel = Cartel.objects.create(
            parcela=self.parcela,
            propietario_cartel=self.propietario_cartel,
            kobo_id="kobo-test-plan-1",
            estado_procesamiento="ok",
            estado_registro="activo",
            superficie_m2=10,
        )
        self.liquidacion = Liquidacion.objects.create(
            cartel=self.cartel,
            propietario_cartel=self.propietario_cartel,
            propietario_terreno=self.propietario_terreno,
            superficie_m2=Decimal("10.0000"),
            estado="conformada",
            monto_total=Decimal("10000.00"),
        )

    def test_form_acepta_tasa_con_coma_y_la_normaliza(self):
        form = PlanDePagoForm(data={
            "monto_anticipo": "1000,50",
            "cantidad_cuotas": 3,
            "tasa_financiacion_mensual": "0,01",
            "observaciones": "",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["monto_anticipo"], Decimal("1000.50"))
        self.assertEqual(form.cleaned_data["tasa_financiacion_mensual"], Decimal("0.0100"))

    def test_generar_cuotas_usa_tasa_exacta_normalizada(self):
        plan = PlanDePago(
            liquidacion=self.liquidacion,
            monto_deuda_base=Decimal("10000.00"),
            monto_anticipo=Decimal("1000.00"),
            cantidad_cuotas=3,
            tasa_financiacion_mensual=Decimal("0.0100"),
        )
        plan.save()

        plan.generar_cuotas()

        cuotas = list(plan.cuotas.order_by("nro_cuota"))
        self.assertEqual(len(cuotas), 4)
        self.assertEqual(cuotas[0].monto_interes, Decimal("0.00"))
        self.assertEqual(cuotas[1].monto_interes, Decimal("90.00"))
