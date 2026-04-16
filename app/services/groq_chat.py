import json
from urllib import request, error
from app.config import GROQ_API_KEY, GROQ_MODEL
from app.database import SessionLocal
from app.models.conversation import ConversationTurn
from app.models.knowledge import Knowledge
from app.utils.ai_config import get_ai_config

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MENU_HINT = "\n\nEscribe menu para volver."

def _get_relevant_knowledge(user_message: str) -> str:
    """Busca en la base de conocimiento entradas cuyas tags coincidan con palabras del mensaje."""
    db = SessionLocal()
    try:
        words = set(user_message.lower().split())
        all_knowledge = db.query(Knowledge).filter(Knowledge.enabled == True).all()
        relevant = []
        for k in all_knowledge:
            if not k.tags:
                continue
            tags = set(t.strip().lower() for t in k.tags.split(','))
            if words.intersection(tags):
                relevant.append(k.content)
        db.close()
        if relevant:
            return "Información adicional que podés usar:\n" + "\n".join(relevant)
        return ""
    except Exception as e:
        print(f"Error buscando conocimiento: {e}")
        return ""

def ask_groq(user_phone: str, user_message: str) -> str:
    if not GROQ_API_KEY:
        return "No puedo hablar con la IA todavía porque falta configurar `GROQ_API_KEY`." + MENU_HINT

    config = get_ai_config()
    system_prompt = config["system_prompt"]
    temperature = config["temperature"]
    max_tokens = config["max_tokens"]
    context_window = config["context_window"]

    db = SessionLocal()
    try:
        # Obtener historial reciente
        history = db.query(ConversationTurn)\
            .filter(ConversationTurn.user_phone == user_phone)\
            .order_by(ConversationTurn.created_at.desc())\
            .limit(context_window * 2)\
            .all()
        history = list(reversed(history))  # orden cronológico

        # Buscar conocimiento relevante
        knowledge_text = _get_relevant_knowledge(user_message)
        if knowledge_text:
            system_prompt = system_prompt + "\n\n" + knowledge_text

        # Construir mensajes para Groq
        messages = [{"role": "system", "content": system_prompt}]
        for turn in history:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": user_message})

        # Guardar mensaje del usuario en historial
        user_turn = ConversationTurn(user_phone=user_phone, role="user", content=user_message)
        db.add(user_turn)
        db.commit()

        # Llamar a Groq
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
                "User-Agent": "Mozilla/5.0",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))

        ai_response = data["choices"][0]["message"]["content"].strip()
        if not ai_response:
            ai_response = "La IA se quedó callada esta vez." + MENU_HINT
        else:
            ai_response += MENU_HINT

        # Guardar respuesta en historial
        assistant_turn = ConversationTurn(user_phone=user_phone, role="assistant", content=ai_response)
        db.add(assistant_turn)
        db.commit()

        return ai_response

    except Exception as e:
        print(f"Error en ask_groq: {e}")
        db.rollback()
        return "La IA no está respondiendo ahora. Probá de nuevo en un rato." + MENU_HINT
    finally:
        db.close()