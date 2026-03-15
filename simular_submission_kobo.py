"""
simular_submission_kobo.py

Sube una submission de prueba a KoboToolbox con una foto de cartel real.

Uso:
    python3 simular_submission_kobo.py ruta/a/foto.jpg --distancia 11 --tipo publicitario --estado bueno

Requiere: requests
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import requests

KOBO_TOKEN = "d1eee51fe82dc1865d78a129d24bdd7489d35898"
ASSET_UID  = "aZ8CQG5pB8FF2MpX4on7uV"
KOBO_URL   = "https://kf.kobotoolbox.org"
HEADERS    = {"Authorization": f"Token {KOBO_TOKEN}"}


def subir_submission(
    ruta_foto: str,
    distancia: float,
    lat: float,
    lon: float,
    tipo_cartel: str,
    estado_cartel: str,
    observaciones: str,
) -> dict:
    """
    Sube una submission a Kobo usando la API de envío de formularios.
    Adjunta la foto como archivo multipart.
    """

    # ── 1. Validar foto ──────────────────────────────────────────────────────
    if not os.path.isfile(ruta_foto):
        print(f"ERROR: No existe el archivo {ruta_foto}")
        sys.exit(1)

    nombre_foto = os.path.basename(ruta_foto)

    # ── 2. Construir el XML de submission (formato ODK/XForm) ────────────────
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    geopoint = f"{lat} {lon} 0 0"   # lat lon altitud precision

    xml = f"""<?xml version='1.0' ?>
<{ASSET_UID} id="{ASSET_UID}">
  <formhub>
    <uuid>{ASSET_UID}</uuid>
  </formhub>
  <start>{ahora}</start>
  <end>{ahora}</end>
  <foto_cartel>{nombre_foto}</foto_cartel>
  <ubicacion>{geopoint}</ubicacion>
  <distancia_cartel>{distancia}</distancia_cartel>
  <tipo_cartel>{tipo_cartel}</tipo_cartel>
  <estado_cartel>{estado_cartel}</estado_cartel>
  <observaciones>{observaciones}</observaciones>
  <__version__>vDummy</__version__>
</{ASSET_UID}>"""

    # ── 3. Endpoint de submissions ───────────────────────────────────────────
    url = f"{KOBO_URL}/api/v2/assets/{ASSET_UID}/hooks/"
    # Kobo acepta submissions via el endpoint legacy de KoBoCAT:
    url_submit = f"{KOBO_URL}/api/v2/assets/{ASSET_UID}/submissions/"

    # El endpoint correcto para subir submissions con adjuntos es el de ODK:
    url_odk = f"{KOBO_URL}/submission"

    # ── 4. Preparar multipart ────────────────────────────────────────────────
    with open(ruta_foto, "rb") as f:
        contenido_foto = f.read()

    # Detectar mime type por extensión
    ext = os.path.splitext(nombre_foto)[1].lower()
    mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".png": "image/png",  ".heic": "image/heic"}
    mime = mime_types.get(ext, "image/jpeg")

    files = {
        "xml_submission_file": ("submission.xml", xml.encode("utf-8"), "text/xml"),
        nombre_foto: (nombre_foto, contenido_foto, mime),
    }

    # ── 5. Enviar ────────────────────────────────────────────────────────────
    print(f"Subiendo submission con foto: {nombre_foto}")
    print(f"  Distancia: {distancia}m")
    print(f"  GPS: {lat}, {lon}")
    print(f"  Tipo: {tipo_cartel} | Estado: {estado_cartel}")
    print(f"  Observaciones: {observaciones}")
    print()

    r = requests.post(url_odk, headers=HEADERS, files=files, timeout=60)

    print(f"HTTP Status: {r.status_code}")
    print(f"Respuesta: {r.text[:500]}")

    if r.status_code in (200, 201, 202):
        print("\n✓ Submission subida correctamente.")
        return {"ok": True, "status": r.status_code}
    else:
        print(f"\n✗ Error al subir. Status: {r.status_code}")
        return {"ok": False, "status": r.status_code, "detalle": r.text}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sube una submission de prueba a KoboToolbox"
    )
    parser.add_argument("foto", help="Ruta a la foto del cartel (jpg/png)")
    parser.add_argument("--distancia", type=float, default=10.0,
                        help="Distancia al cartel en metros (default: 10)")
    parser.add_argument("--lat",  type=float, default=-34.785951,
                        help="Latitud GPS (default: Dolores, BA)")
    parser.add_argument("--lon",  type=float, default=-57.678889,
                        help="Longitud GPS (default: Dolores, BA)")
    parser.add_argument("--tipo", default="publicitario",
                        choices=["publicitario", "politico", "senalizacion",
                                 "informativo", "otro"],
                        help="Tipo de cartel (default: publicitario)")
    parser.add_argument("--estado", default="bueno",
                        choices=["bueno", "regular", "malo"],
                        help="Estado físico del cartel (default: bueno)")
    parser.add_argument("--obs", default="Submission de prueba desde script",
                        help="Observaciones")

    args = parser.parse_args()

    resultado = subir_submission(
        ruta_foto=args.foto,
        distancia=args.distancia,
        lat=args.lat,
        lon=args.lon,
        tipo_cartel=args.tipo,
        estado_cartel=args.estado,
        observaciones=args.obs,
    )

    sys.exit(0 if resultado["ok"] else 1)
