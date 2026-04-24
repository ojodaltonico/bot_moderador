import re
import unicodedata


SALE_KEYWORDS = [
    "vendo", "vendiendo", "venta", "compro", "comprar", "permuto",
    "permuta", "alquilo", "alquiler", "alquilo", "se vende",
    "anticipadas", "entrada", "entradas", "precio", "precios",
    "promo", "promocion", "promocion", "oferta", "negociable",
    "liquidacion", "remato", "publicidad", "publico", "publicá",
    "publica", "encargo", "encargos", "delivery", "reparto"
]

SALE_HINT_PATTERNS = [
    "al priv", "al privado", "inbox", "mp", "md", "por privado",
    "consultas al privado", "reserva", "stock", "talle", "unidad",
    "seña", "envio", "envios", "promo", "2x1", "$", "usd"
]

QUESTION_STARTERS = [
    "alguien", "donde", "dónde", "como", "cómo", "cuando", "cuándo",
    "quien", "quién", "que", "qué", "cual", "cuál", "hay",
    "saben", "alguno", "alguna", "me pasan", "me dicen", "tienen"
]

QUESTION_FRAGMENT_PATTERNS = [
    "entre q", "entre que", "que calle", "qué calle", "numero de",
    "número de", "a que hora", "a qué hora", "donde queda", "dónde queda",
    "se sabe", "alguien sabe", "me dirian", "me dirían", "tienen el numero",
    "tienen el número"
]

COMPLAINT_PATTERNS = [
    "no funciona", "no anda", "problema", "mal servicio", "queja",
    "reclamo", "error", "me paso", "me pasó", "no me responde",
    "se corto", "se cortó", "sin luz", "explosion", "explosión"
]

GREETING_PATTERNS = [
    "hola", "buen dia", "buen día", "buenas", "buenas tardes",
    "buenas noches", "buen finde", "hola grupo", "hola gente"
]

LINK_RE = re.compile(r"(https?://|www\.|wa\.me/)", re.IGNORECASE)
MONEY_RE = re.compile(r"(\$\s?\d+|\d+\s?(mil|k)\b)", re.IGNORECASE)


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _normalize_text(text: str) -> str:
    text = _strip_accents(text.lower().strip())
    text = re.sub(r"\s+", " ", text)
    return text


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _looks_like_question(original_text: str, normalized_text: str) -> bool:
    if not normalized_text:
        return False

    if "?" in original_text:
        return True

    if _contains_any(normalized_text, QUESTION_FRAGMENT_PATTERNS):
        return True

    if any(normalized_text.startswith(starter + " ") or normalized_text == starter for starter in QUESTION_STARTERS):
        return True

    short_question = len(normalized_text.split()) <= 5 and any(
        normalized_text.startswith(prefix) for prefix in ["donde", "como", "cuando", "entre q", "entre que", "quien", "que", "cual"]
    )
    return short_question


def _looks_like_sale(normalized_text: str, contains_link: bool) -> bool:
    if not normalized_text:
        return False

    has_sale_keyword = _contains_any(normalized_text, SALE_KEYWORDS)
    has_sale_hint = _contains_any(normalized_text, SALE_HINT_PATTERNS)
    has_money = bool(MONEY_RE.search(normalized_text))

    if has_sale_keyword and (has_sale_hint or has_money):
        return True

    if has_sale_keyword:
        return True

    if has_sale_hint and has_money:
        return True

    if contains_link and (has_sale_hint or has_money):
        return True

    return False


def analyze_message(message_type: str, content: str | None = None, media_caption: str | None = None) -> dict:
    base_text = (content or media_caption or "").strip()
    normalized = _normalize_text(base_text)
    contains_link = bool(LINK_RE.search(base_text))
    contains_question = _looks_like_question(base_text, normalized)
    text_length = len(base_text) if base_text else 0

    category_label = "GENERAL"
    intent_label = "GENERAL"

    if message_type in {"image", "video", "audio", "document", "sticker"}:
        category_label = "MEDIA"
        intent_label = "MEDIA_SHARE"

    if _looks_like_sale(normalized, contains_link):
        category_label = "SALE"
        intent_label = "OFFER"
    elif normalized and _contains_any(normalized, COMPLAINT_PATTERNS):
        category_label = "COMPLAINT"
        intent_label = "COMPLAINT"
    elif normalized and contains_question:
        category_label = "QUESTION"
        intent_label = "INFO_REQUEST"
    elif normalized and _contains_any(normalized, GREETING_PATTERNS):
        category_label = "GREETING"
        intent_label = "SOCIAL"
    elif contains_link:
        category_label = "LINK"
        intent_label = "SHARE_LINK"
    elif message_type == "text" and normalized:
        category_label = "CHAT"
        intent_label = "GENERAL"

    return {
        "category_label": category_label,
        "intent_label": intent_label,
        "intent_source": "heuristic_v2",
        "contains_question": contains_question,
        "contains_link": contains_link,
        "content_length": text_length or None
    }
