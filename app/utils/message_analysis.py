import re


SALES_KEYWORDS = [
    "vendo", "venta", "compro", "precio", "promo",
    "oferta", "negocio", "negociable", "venda", "comprar",
    "vendiendo", "remato", "liquidacion"
]

QUESTION_PATTERNS = [
    "?", "como", "cómo", "cuando", "cuándo", "donde", "dónde",
    "que", "qué", "quien", "quién", "se puede", "alguien sabe"
]

COMPLAINT_PATTERNS = [
    "no funciona", "no anda", "problema", "mal", "queja",
    "me pasó", "me paso", "no me", "error", "reclamo"
]

GREETING_PATTERNS = [
    "hola", "buen dia", "buen día", "buenas", "buenas tardes",
    "buenas noches", "hey", "buen finde"
]

LINK_RE = re.compile(r"(https?://|www\.|wa\.me/)", re.IGNORECASE)


def analyze_message(message_type: str, content: str | None = None, media_caption: str | None = None) -> dict:
    base_text = (content or media_caption or "").strip()
    normalized = base_text.lower()
    contains_link = bool(LINK_RE.search(base_text))
    contains_question = any(pattern in normalized for pattern in QUESTION_PATTERNS)
    text_length = len(base_text) if base_text else 0

    category_label = "GENERAL"
    intent_label = "GENERAL"

    if message_type in {"image", "video", "audio", "document", "sticker"}:
        category_label = "MEDIA"
        intent_label = "MEDIA_SHARE"

    if contains_link:
        category_label = "LINK"
        intent_label = "SHARE_LINK"

    if normalized and any(keyword in normalized for keyword in SALES_KEYWORDS):
        category_label = "SALE"
        intent_label = "OFFER"
    elif normalized and any(pattern in normalized for pattern in COMPLAINT_PATTERNS):
        category_label = "COMPLAINT"
        intent_label = "COMPLAINT"
    elif normalized and contains_question:
        category_label = "QUESTION"
        intent_label = "INFO_REQUEST"
    elif normalized and any(pattern in normalized for pattern in GREETING_PATTERNS):
        category_label = "GREETING"
        intent_label = "SOCIAL"
    elif message_type == "text" and normalized:
        category_label = "CHAT"
        intent_label = "GENERAL"

    return {
        "category_label": category_label,
        "intent_label": intent_label,
        "intent_source": "heuristic_v1",
        "contains_question": contains_question,
        "contains_link": contains_link,
        "content_length": text_length or None
    }
