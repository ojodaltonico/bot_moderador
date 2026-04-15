import os

from app.utils.phone import normalize_phone

GROUP_ID = "120363200443002725@g.us"

# IMPORTANTE: Usar el LID de WhatsApp del admin, NO el número de teléfono
# Tu LID según los logs es: 69634422268027
ADMIN_PHONE = "69634422268027"

MEDIA_IMAGES_PATH = "media/temp/images"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
