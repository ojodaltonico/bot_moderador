import time
from app.database import SessionLocal
from app.models.ai_settings import AISettings

_cached_config = None
_last_fetch = 0
CACHE_TTL = 60  # segundos

def get_ai_config():
    global _cached_config, _last_fetch
    now = time.time()
    if _cached_config is None or (now - _last_fetch) > CACHE_TTL:
        db = SessionLocal()
        try:
            config = db.query(AISettings).filter(AISettings.id == 1).first()
            if not config:
                # Crear configuración por defecto
                config = AISettings(
                    id=1,
                    system_prompt="Sos la asistente del bot moderador de WhatsApp. Respondés en español rioplatense, con tono chusma, simpático y conversador, como alguien que siempre está al tanto de todo, pero sin ser agresiva ni pesada. Sé útil, clara y breve. No inventes acciones del bot ni sanciones. Si no sabés algo, decilo con honestidad.",
                    temperature=0.9,
                    max_tokens=400,
                    context_window=10
                )
                db.add(config)
                db.commit()
                db.refresh(config)
            _cached_config = {
                "system_prompt": config.system_prompt,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "context_window": config.context_window
            }
            _last_fetch = now
        finally:
            db.close()
    return _cached_config