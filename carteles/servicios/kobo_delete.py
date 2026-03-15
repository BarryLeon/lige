"""
kobo_delete.py
Borra una submission de KoboToolbox via API.
Se usa cuando el operador tiene que volver a cargar el formulario.
"""

import logging
import requests

logger = logging.getLogger(__name__)

KOBO_TOKEN   = "d1eee51fe82dc1865d78a129d24bdd7489d35898"
ASSET_UID    = "aZ8CQG5pB8FF2MpX4on7uV"
KOBO_BASE    = "https://kf.kobotoolbox.org"
HEADERS      = {"Authorization": f"Token {KOBO_TOKEN}"}


def borrar_submission_kobo(kobo_id: str) -> dict:
    """
    Borra una submission de Kobo por su _id.

    Returns:
        dict con ok (bool) y detalle (str)
    """
    if not kobo_id:
        return {"ok": False, "detalle": "kobo_id vacío"}

    url = f"{KOBO_BASE}/api/v2/assets/{ASSET_UID}/data/{kobo_id}/"

    try:
        r = requests.delete(url, headers=HEADERS, timeout=15)
        if r.status_code == 204:
            logger.info(f"Submission {kobo_id} borrada de Kobo.")
            return {"ok": True, "detalle": "Borrado correctamente de KoboToolbox"}
        else:
            msg = f"Kobo respondió {r.status_code}: {r.text[:200]}"
            logger.warning(msg)
            return {"ok": False, "detalle": msg}
    except Exception as exc:
        msg = f"Error al conectar con Kobo: {exc}"
        logger.error(msg)
        return {"ok": False, "detalle": msg}