import json
from urllib import error, request

from app.config import GROQ_API_KEY, GROQ_MODEL


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MENU_HINT = "\n\nEscribe menu para volver."
SYSTEM_PROMPT = (
    "Sos la asistente del bot moderador de WhatsApp. "
    "Respondés en español rioplatense, con tono chusma, simpático y conversador, "
    "como alguien que siempre está al tanto de todo, pero sin ser agresiva ni pesada. "
    "Sé útil, clara y breve. No inventes acciones del bot ni sanciones. "
    "Si no sabés algo, decilo con honestidad."
)


def ask_groq(user_message: str) -> str:
    if not GROQ_API_KEY:
        return (
            "No puedo hablar con la IA todavía porque falta configurar `GROQ_API_KEY`."
            + MENU_HINT
        )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.9,
        "max_tokens": 400,
    }

    req = request.Request(
        GROQ_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"Error Groq HTTP {exc.code}: {detail}")
        return "La IA está medio en cualquiera ahora mismo. Probá de nuevo en un ratito." + MENU_HINT
    except Exception as exc:
        print(f"Error Groq: {exc}")
        return "La IA no está respondiendo ahora. Probá de nuevo en un rato." + MENU_HINT

    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        print(f"Respuesta Groq inesperada: {data}")
        return "La IA me contestó cualquier cosa y no pude leerla bien." + MENU_HINT

    if not content:
        return "La IA se quedó callada esta vez." + MENU_HINT

    return content + MENU_HINT
