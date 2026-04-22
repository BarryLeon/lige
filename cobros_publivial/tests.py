from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tasas_marchiquita.models import ArchivoImportacion, Liquidacion

from .models import Factura, HonorarioLiquidacion
from .services import liquidaciones_marchiquita_sin_factura


class MarchiquitaCobrosTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='secret123',
        )
        self.importacion = ArchivoImportacion.objects.create(
            archivo=b'test',
            nombre_archivo='abril.xlsx',
            periodo=date(2026, 4, 1),
            procesado=True,
        )
        self.liquidacion = Liquidacion.objects.create(
            importacion=self.importacion,
            total_pago_deuda=Decimal('1000.00'),
            total_anticipos=Decimal('200.00'),
            total_cuotas=Decimal('300.00'),
            total_cobrado=Decimal('1500.00'),
            comision=Decimal('375.00'),
            pdf=b'pdf',
        )

    def test_liquidaciones_marchiquita_sin_factura_incluye_pendientes(self):
        pendientes = list(liquidaciones_marchiquita_sin_factura())
        self.assertEqual(pendientes, [self.liquidacion])

    def test_marchiquita_post_registra_factura_con_importe_de_liquidacion(self):
        self.client.login(username='tester', password='secret123')

        response = self.client.post(
            reverse('cobros_publivial:marchiquita_honorarios'),
            {
                'liquidacion_id': str(self.liquidacion.pk),
                'numero_factura': 'A-0001-00000001',
                'fecha_emision': '2026-04-21',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        honorario = HonorarioLiquidacion.objects.get(
            liquidacion_marchiquita_id=self.liquidacion.pk
        )
        factura = Factura.objects.get(numero='A-0001-00000001')

        self.assertEqual(honorario.base_calculo, Decimal('375.00'))
        self.assertEqual(honorario.porcentaje_aplicado, Decimal('100.00'))
        self.assertEqual(honorario.monto_honorario, Decimal('375.00'))
        self.assertEqual(factura.monto_total, Decimal('375.00'))
        self.assertFalse(
            liquidaciones_marchiquita_sin_factura().filter(pk=self.liquidacion.pk).exists()
        )
