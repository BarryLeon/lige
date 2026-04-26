"""
Utilidades para integrar PyTorch en entornos sin GPU CUDA disponible.
"""

from contextlib import contextmanager
import logging
import warnings

logger = logging.getLogger(__name__)

_WARNING_CUDA_SIN_DRIVER = r"CUDA initialization: CUDA driver initialization failed.*"
_WARNING_PIN_MEMORY_SIN_ACELERADOR = r".*pin_memory.*no accelerator is found.*"


@contextmanager
def suprimir_warnings_torch_cpu():
    """Silencia warnings esperables cuando PyTorch corre en CPU."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=_WARNING_CUDA_SIN_DRIVER,
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=_WARNING_PIN_MEMORY_SIN_ACELERADOR,
            category=UserWarning,
        )
        yield


def cuda_disponible() -> bool:
    """
    Devuelve True si CUDA está disponible y usable; si no, cae limpiamente a CPU.
    """
    try:
        import torch
    except Exception:
        return False

    try:
        with suprimir_warnings_torch_cpu():
            return bool(torch.cuda.is_available())
    except Exception as exc:
        logger.warning(f"No se pudo inicializar CUDA; se usará CPU. Detalle: {exc}")
        return False
