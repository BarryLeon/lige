import math

import numpy as np
from django.test import SimpleTestCase

from carteles.servicios.cartel_detector import (
    _diagnosticar_consistencia_superficie,
    _estimar_superficie_por_homografia,
    _normalizar_esquinas_manuales,
    _ordenar_esquinas,
)


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
