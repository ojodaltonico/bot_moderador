import json
import re
import locale
from urllib import request, error
from datetime import datetime
from app.config import GROQ_API_KEY, GROQ_MODEL
from app.database import SessionLocal
from app.models.conversation import ConversationTurn
from app.models.knowledge import Knowledge
from app.utils.ai_config import get_ai_config

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MENU_HINT = "\n\nEscribe menu para volver."

# Configurar locale para fechas en español (si está disponible)
try:
    locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
except:
    pass  # Fallback manual más abajo

# Diccionarios de respaldo para fechas en español
DIAS = {
    "Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles",
    "Thursday": "jueves", "Friday": "viernes", "Saturday": "sábado",
    "Sunday": "domingo"
}
MESES = {
    "January": "enero", "February": "febrero", "March": "marzo",
    "April": "abril", "May": "mayo", "June": "junio",
    "July": "julio", "August": "agosto", "September": "septiembre",
    "October": "octubre", "November": "noviembre", "December": "diciembre"
}

def _fecha_en_espanol():
    """Devuelve fecha formateada en español, incluso si locale falla."""
    now = datetime.now()
    try:
        return now.strftime("%A %d de %B")
    except:
        dia_en = now.strftime("%A")
        mes_en = now.strftime("%B")
        dia_es = DIAS.get(dia_en, dia_en)
        mes_es = MESES.get(mes_en, mes_en)
        return f"{dia_es} {now.day} de {mes_es}"

def _call_groq(messages, temperature=0.7, max_tokens=200):
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = request.Request(
        GROQ_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; WhatsAppBot/1.0)"
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        print(f"HTTP error {e.code}: {error_body}")
        raise
    except error.URLError as e:
        print(f"URL error: {e.reason}")
        raise

def _get_relevant_knowledge(user_message: str) -> str:
    """Busca conocimiento relevante por coincidencia de tags."""
    db = SessionLocal()
    try:
        text = user_message.lower()
        all_knowledge = db.query(Knowledge).filter(Knowledge.enabled == True).all()

        relevant = []
        for k in all_knowledge:
            if not k.tags:
                continue
            tags = [t.strip().lower() for t in k.tags.split(",")]
            if any(tag in text for tag in tags):
                relevant.append(k.content)

        return "\n".join(relevant) if relevant else ""
    except Exception as e:
        print(f"Error en _get_relevant_knowledge: {e}")
        return ""
    finally:
        db.close()

def _classify_intent(user_message: str) -> str:
    """Clasifica la intención del mensaje usando Groq."""
    classifier_prompt = """
Clasificá la intención del mensaje del usuario en UNA de estas categorías:
- INFO: pregunta sobre hechos, datos, reglas, información concreta.
- CHUSMERIO: chisme, rumor, tono juguetón o curioso.
- QUEJA: reclamo, insatisfacción, problema.
- GENERAL: cualquier otra cosa (saludo, opinión, conversación normal).

Respondé ÚNICAMENTE con la palabra: INFO, CHUSMERIO, QUEJA o GENERAL.
"""

    messages = [
        {"role": "system", "content": classifier_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        respuesta = _call_groq(messages, temperature=0, max_tokens=10)
        # Limpiar: quedarse solo con letras mayúsculas
        intent = re.sub(r'[^A-Z]', '', respuesta.upper())
        if intent:
            intent = intent.split()[0]
        if intent not in ["INFO", "CHUSMERIO", "QUEJA", "GENERAL"]:
            intent = "GENERAL"
        return intent
    except Exception as e:
        print(f"Error clasificando intención: {e}")
        return "GENERAL"

def ask_groq(user_phone: str, user_message: str) -> str:
    """Procesa mensaje del usuario, clasifica intención y devuelve respuesta."""
    if not GROQ_API_KEY:
        return "No puedo hablar con la IA todavía porque falta configurar GROQ_API_KEY." + MENU_HINT

    config = get_ai_config()
    base_prompt = config["system_prompt"]
    temperature = config["temperature"]
    max_tokens = config["max_tokens"]
    context_window = config["context_window"]

    db = SessionLocal()

    try:
        # 1. Clasificar intención
        intent = _classify_intent(user_message)
        print(f"[AI] Intent detectado: {intent}")

        # 2. Obtener historial
        history = db.query(ConversationTurn)\
            .filter(ConversationTurn.user_phone == user_phone)\
            .order_by(ConversationTurn.created_at.desc())\
            .limit(context_window * 2)\
            .all()
        history = list(reversed(history))

        # 3. Conocimiento relevante
        knowledge_text = _get_relevant_knowledge(user_message)

        # 4. Fecha en español
        fecha_str = _fecha_en_espanol()

        # 5. Instrucción según intención (sin redundancia)
        modo_instruccion = {
            "INFO": "Ahora actuá en MODO INFO: respondé directo, solo datos reales, sin chusmerío.",
            "CHUSMERIO": "Ahora actuá en MODO CHUSMERIO: sé curioso, cómplice, repreguntá, con tono juguetón.",
            "QUEJA": "Ahora actuá en MODO QUEJA: explicá con calma, no discutas, ofrecé escribir 'apelar' para revisión.",
            "GENERAL": "Comportamiento normal, según tu personalidad base."
        }[intent]

        # 6. Construir system prompt final
        system_parts = [base_prompt, modo_instruccion, f"Hoy es {fecha_str}."]
        if knowledge_text:
            system_parts.append("INFORMACIÓN DISPONIBLE (usala si es relevante):\n" + knowledge_text)

        system_final = "\n\n".join(system_parts)

        # 7. Armar mensajes para la IA
        messages = [{"role": "system", "content": system_final}]
        for turn in history:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": user_message})

        # 8. Guardar mensaje del usuario en DB (sin hint)
        user_turn = ConversationTurn(user_phone=user_phone, role="user", content=user_message)
        db.add(user_turn)
        db.commit()

        # 9. Obtener respuesta de Groq
        ai_response = _call_groq(messages, temperature, max_tokens)

        if not ai_response:
            ai_response = "Se quedó pensando…"

        # 10. Guardar respuesta en DB (SIN el MENU_HINT)
        assistant_turn = ConversationTurn(user_phone=user_phone, role="assistant", content=ai_response)
        db.add(assistant_turn)
        db.commit()

        # 11. Devolver respuesta al usuario CON el hint
        return ai_response + MENU_HINT

    except Exception as e:
        print(f"Error en ask_groq: {e}")
        db.rollback()
        return "La IA no está respondiendo ahora. Probá de nuevo en un rato." + MENU_HINT
    finally:
        db.close()