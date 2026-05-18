import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass


SALE_PATTERNS = [
    "vendo", "venta", "vendoo", "alquilo", "alquiler", "se alquila",
    "tomamos pedidos", "pedido", "pedidos", "disponible", "disponibles",
    "precio", "promo", "promocion", "2x1", "stock", "delivery",
    "trabajos en", "se realizan trabajos", "servicio", "servicios",
    "lifting", "depilacion", "canelones", "leña", "lena"
]

COMMUNITY_ALLOW_PATTERNS = [
    "perro perdido", "perra perdida", "gatito", "gatita", "mascota",
    "machito", "hembrita", "cachorro", "cachorra",
    "aparecio", "apareció", "buscamos a sus duenos", "buscamos a sus dueños",
    "se perdio", "se perdió", "adopte", "adoptar", "llaves",
    "se encontraron", "encontre", "encontré", "dueño", "dueno",
    "accidente", "moto", "tormenta", "clima", "calle cortada"
]

JOB_PATTERNS = [
    "busco trabajo", "busca trabajo", "buscando trabajo", "busca de trabajo",
    "curriculum", "currículum", "cv", "referencia", "referencias"
]

SOLICITED_SALE_PATTERNS = [
    "quien vende", "quién vende", "alguien vende", "donde venden",
    "dónde venden", "donde consigo", "dónde consigo", "necesito comprar",
    "busco comprar", "alguien tiene", "me pasan precio", "recomiendan"
]


@dataclass
class ImageAnalysisResult:
    category_label: str
    intent_label: str
    should_flag: bool
    priority: int
    reason: str
    confidence: int
    ocr_text: str | None = None
    context_text: str | None = None


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_text(text: str | None) -> str:
    text = _strip_accents((text or "").lower())
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contains_any(text: str, patterns: list[str]) -> bool:
    return any(normalize_text(pattern) in text for pattern in patterns)


def ocr_image(image_path: str) -> str:
    if not image_path or not os.path.exists(image_path):
        return ""
    if not shutil.which("tesseract"):
        return ""

    for lang in ("spa+eng", "spa", "eng"):
        try:
            result = subprocess.run(
                ["tesseract", image_path, "stdout", "-l", lang],
                capture_output=True,
                text=True,
                timeout=12,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            continue
    return ""


def analyze_image(caption: str | None, ocr_text: str | None, context_items: list[dict]) -> ImageAnalysisResult:
    caption_norm = normalize_text(caption)
    ocr_norm = normalize_text(ocr_text)
    own_text = " ".join(part for part in [caption_norm, ocr_norm] if part)

    context_lines = []
    for item in context_items:
        preview = normalize_text(item.get("text"))
        if preview:
            context_lines.append(preview)
    context_text = " | ".join(context_lines[:10])

    has_sale = contains_any(own_text, SALE_PATTERNS) or bool(re.search(r"(\$\s?\d+|\d+\s?(mil|k)\b)", own_text))
    is_allowed_community = contains_any(own_text, COMMUNITY_ALLOW_PATTERNS)
    is_job_search = contains_any(own_text, JOB_PATTERNS)
    solicited_sale = has_sale and contains_any(context_text, SOLICITED_SALE_PATTERNS)

    if is_allowed_community:
        if "llave" in own_text:
            return ImageAnalysisResult("FOUND_OBJECT", "FOUND_ITEM", False, 5, "objeto encontrado", 90, ocr_text, context_text)
        if any(term in own_text for term in ["perro", "perra", "gato", "gatito", "gatita", "mascota"]):
            return ImageAnalysisResult("LOST_PET", "HELP_REQUEST", False, 5, "mascota perdida/encontrada", 90, ocr_text, context_text)
        return ImageAnalysisResult("COMMUNITY_INFO", "INFO_SHARE", False, 5, "aviso comunitario", 80, ocr_text, context_text)

    if is_job_search:
        return ImageAnalysisResult("JOB_SEARCH", "HELP_REQUEST", False, 5, "busqueda laboral permitida", 80, ocr_text, context_text)

    if solicited_sale:
        return ImageAnalysisResult("SALE", "OFFER", False, 4, "respuesta a consulta previa de compra/venta", 75, ocr_text, context_text)

    if has_sale:
        return ImageAnalysisResult("SALE", "OFFER", True, 1, "publicidad/venta directa detectada", 85, ocr_text, context_text)

    if own_text:
        return ImageAnalysisResult("MEDIA", "MEDIA_SHARE", False, 5, "imagen con texto no comercial", 60, ocr_text, context_text)

    if context_text:
        return ImageAnalysisResult("MEDIA", "MEDIA_SHARE", True, 4, "imagen sin texto propio; revisar con contexto", 35, ocr_text, context_text)

    return ImageAnalysisResult("MEDIA", "MEDIA_SHARE", True, 4, "imagen sin caption ni OCR", 25, ocr_text, context_text)
