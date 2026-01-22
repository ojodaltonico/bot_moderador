from app.config import ADMIN_PHONE
from app.models import Moderator


def is_moderator(db, phone: str) -> bool:
    """
    Verifica si un número es moderador.
    WhatsApp puede enviar números en diferentes formatos.
    """
    if not phone:
        return False

    # Normalizar: quitar espacios y caracteres no numéricos
    import re
    normalized = re.sub(r'\D', '', phone)

    # Verificar contra ADMIN_PHONE (ya normalizado en config)
    if normalized == ADMIN_PHONE:
        return True

    # Verificar en la base de datos
    # Primero con el número normalizado
    mod = db.query(Moderator).filter(Moderator.phone == normalized, Moderator.active == True).first()
    if mod:
        return True

    # Si no encuentra, intentar también con el original
    if phone != normalized:
        mod = db.query(Moderator).filter(Moderator.phone == phone, Moderator.active == True).first()
        if mod:
            return True

    return False