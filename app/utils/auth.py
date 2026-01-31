from app.config import ADMIN_PHONE
from app.models import Moderator
import re


def normalize_phone(phone: str) -> str:
    """Normaliza números de teléfono quitando caracteres no numéricos"""
    if not phone:
        return ""
    return re.sub(r'\D', '', phone)


def is_moderator(db, phone: str) -> bool:
    """
    Verifica si un número es moderador.
    Busca tanto por LID como por número real.
    """
    if not phone:
        return False

    # Normalizar número
    normalized = normalize_phone(phone)

    # Verificar contra ADMIN_PHONE
    if normalized == str(ADMIN_PHONE):
        return True

    # Buscar en la base de datos por LID o por número real
    mod = db.query(Moderator).filter(
        Moderator.active == True,
        (
            (Moderator.lid == phone) |  # Buscar por LID completo (ej: 9401733800078)
            (Moderator.phone == phone) |  # Buscar por número original
            (Moderator.phone == normalized)  # Buscar por número normalizado
        )
    ).first()

    return mod is not None