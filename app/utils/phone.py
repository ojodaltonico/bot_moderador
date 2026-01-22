import re


def normalize_phone(phone: str) -> str:
    """
    Normaliza números de teléfono para que coincidan con el formato de WhatsApp.
    WhatsApp Web suele usar formatos como: 69634422268027 (sin código de país)
    """
    if not phone:
        return ""

    # Eliminar todo excepto dígitos
    digits = re.sub(r'\D', '', phone)

    # Si el número es muy largo (>12), probablemente es un ID de WhatsApp
    # Los dejamos tal cual
    if len(digits) > 12:
        return digits

    # Para números argentinos normales
    if digits.startswith('549') and len(digits) == 12:
        return digits[2:]  # Quita '54'
    elif digits.startswith('54') and len(digits) == 11:
        digits = digits[2:]
        if not digits.startswith('9'):
            digits = '9' + digits
        return digits
    elif len(digits) == 10 and not digits.startswith('9'):
        return '9' + digits

    return digits