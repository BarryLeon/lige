import math
import io
from datetime import datetime
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from PIL import Image as PILImage

from carteles.models import Cartel, Parcela, Persona
from carteles.servicios.cartel_detector import (
    _diagnosticar_consistencia_superficie,
    _estimar_superficie_por_homografia,
    _normalizar_esquinas_manuales,
    _ordenar_esquinas,
)
from carteles.servicios.exportar import (
    _crear_mapa_ubicacion,
    _descargar_tile_osm,
    exportar_pdf_detallado,
)
from carteles.views import _aplicar_filtros_informes, _get_carteles_base_queryset


class GeometriaCartelDetectorTests(SimpleTestCase):
    def test_ordenar_esquinas_devuelve_orden_canonico(self):
        puntos = np.array(
            [[300, 220], [120, 90], [110, 260], [320, 80]],
            dtype=np.float32,
        )

        ordenados = _ordenar_esquinas(puntos)

        esperado = np.array(
            [[120, 90], [320, 80], [300, 220], [110, 260]],
            dtype=np.float32,
        )
        self.assertTrue(np.allclose(ordenados, esperado))

    def test_normalizar_esquinas_manuales_ordena_y_valida(self):
        esquinas = [
            {"x": 320, "y": 80},
            {"x": 110, "y": 260},
            {"x": 300, "y": 220},
            {"x": 120, "y": 90},
        ]

        normalizadas = _normalizar_esquinas_manuales(
            [[p["x"], p["y"]] for p in esquinas]
        )

        esperado = np.array(
            [[120, 90], [320, 80], [300, 220], [110, 260]],
            dtype=np.float32,
        )
        self.assertTrue(np.allclose(normalizadas, esperado))

    def test_homografia_frontal_recupera_medidas(self):
        esquinas = np.array(
            [[640, 260], [1440, 260], [1440, 760], [640, 760]],
            dtype=np.float32,
        )

        estimacion = _estimar_superficie_por_homografia(
            esquinas=esquinas,
            ancho_total=2048,
            alto_total=1152,
            fov_h=69.4,
            distancia_m=18.0,
        )

        self.assertIsNotNone(estimacion)
        self.assertTrue(math.isclose(estimacion["ancho_m"], 9.7271, rel_tol=0.02))
        self.assertTrue(math.isclose(estimacion["alto_m"], 6.0794, rel_tol=0.02))
        self.assertTrue(math.isclose(estimacion["superficie_m2"], 59.1351, rel_tol=0.03))

    def test_diagnostico_homografia_activa_advertencia(self):
        inconsistente, detalle = _diagnosticar_consistencia_superficie(
            metodo_superficie="homografia",
            superficie_bbox_m2=31.7,
            superficie_homografia_m2=36.9,
            zoom_flag=False,
        )

        self.assertTrue(inconsistente)
        self.assertIn("homografía", detalle.lower())


class InformesCartelesTests(TestCase):
    databases = {"default", "carteles"}

    def setUp(self):
        self.propietario_cartel = Persona.objects.create(
            tipo="fisica",
            apellido="Juanez",
            nombre="Mariana",
            cuit_dni="20111111111",
        )
        self.propietario_terreno = Persona.objects.create(
            tipo="juridica",
            razon_social="Terrenos Juan SA",
            cuit_dni="20999999999",
        )
        self.parcela = Parcela.objects.create(
            circunscripcion="1",
            seccion="A",
            parcela_nro="15",
            direccion="Juan B. Justo 123",
            propietario_terreno=self.propietario_terreno,
        )
        self.cartel_ok = Cartel.objects.create(
            parcela=self.parcela,
            propietario_cartel=self.propietario_cartel,
            tipo_cartel="politico",
            estado_cartel="bueno",
            texto_ocr="Juan conduce Mar Chiquita",
            observaciones="Campaña politica",
            estado_procesamiento="ok",
            estado_registro="activo",
            superficie_m2=12.5,
            ancho_m=5.0,
            alto_m=2.5,
            fecha=timezone.make_aware(datetime(2026, 4, 10, 12, 0)),
            lat=-37.74,
            lon=-57.42,
        )
        self.cartel_otro = Cartel.objects.create(
            tipo_cartel="publicitario",
            estado_cartel="regular",
            texto_ocr="Oferta verano",
            estado_procesamiento="ok",
            estado_registro="activo",
            fecha=timezone.make_aware(datetime(2026, 4, 11, 12, 0)),
        )
        self.cartel_solo_terreno = Cartel.objects.create(
            parcela=self.parcela,
            tipo_cartel="informativo",
            estado_cartel="bueno",
            texto_ocr="Cartel sin duenio directo",
            estado_procesamiento="ok",
            estado_registro="activo",
            fecha=timezone.make_aware(datetime(2026, 4, 12, 12, 0)),
        )

    def test_filtros_parciales_por_propietario_unificado(self):
        qs = _aplicar_filtros_informes(_get_carteles_base_queryset(), {
            "propietario": "juan",
            "parcela": "justo",
            "texto_ocr": "conduce",
            "fecha_desde": "",
            "fecha_hasta": "",
            "tipo_cartel": "politico",
            "estado_proc": "ok",
        })

        self.assertQuerySetEqual(qs.order_by("id"), [self.cartel_ok], transform=lambda obj: obj)

    def test_filtro_propietario_encuentra_duenio_de_terreno_sin_duenio_de_cartel(self):
        qs = _aplicar_filtros_informes(_get_carteles_base_queryset(), {
            "propietario": "juan",
            "parcela": "",
            "texto_ocr": "",
            "fecha_desde": "",
            "fecha_hasta": "",
            "tipo_cartel": "",
            "estado_proc": "ok",
        })

        self.assertIn(self.cartel_solo_terreno, qs)

    @patch("carteles.servicios.exportar.requests.get")
    def test_exportar_pdf_detallado_devuelve_un_pdf(self, mock_get):
        _descargar_tile_osm.cache_clear()
        imagen = PILImage.new("RGB", (256, 256), color="white")
        buffer = io.BytesIO()
        imagen.save(buffer, format="PNG")
        contenido_png = buffer.getvalue()

        respuesta = Mock()
        respuesta.content = contenido_png
        respuesta.raise_for_status.return_value = None
        mock_get.return_value = respuesta

        contenido = exportar_pdf_detallado(
            Cartel.objects.filter(pk=self.cartel_ok.pk),
            media_root="",
        )

        self.assertTrue(contenido.startswith(b"%PDF"))
        self.assertGreater(len(contenido), 500)

    @patch("carteles.servicios.exportar.requests.get")
    def test_crear_mapa_ubicacion_devuelve_imagen_png(self, mock_get):
        _descargar_tile_osm.cache_clear()
        imagen = PILImage.new("RGB", (256, 256), color="white")
        buffer = io.BytesIO()
        imagen.save(buffer, format="PNG")

        respuesta = Mock()
        respuesta.content = buffer.getvalue()
        respuesta.raise_for_status.return_value = None
        mock_get.return_value = respuesta

        mapa = _crear_mapa_ubicacion(-37.74, -57.42)

        self.assertIsNotNone(mapa)
        self.assertTrue(mapa.getvalue().startswith(b"\x89PNG"))

    @patch("carteles.servicios.exportar.requests.get", side_effect=Exception("sin red"))
    def test_exportar_pdf_detallado_funciona_si_falla_el_mapa(self, _mock_get):
        _descargar_tile_osm.cache_clear()
        contenido = exportar_pdf_detallado(
            Cartel.objects.filter(pk=self.cartel_ok.pk),
            media_root="",
        )

        self.assertTrue(contenido.startswith(b"%PDF"))
