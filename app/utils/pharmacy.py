import os
import re
from datetime import datetime, date

PHARMACY_SHIFT_CHANGE_HOUR = int(os.getenv("PHARMACY_SHIFT_CHANGE_HOUR", "8"))

DAY_NAMES = [
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"
]

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _normalize(text: str) -> str:
    return (
        text.replace("MIÉRCOLES", "MIERCOLES")
        .replace("miércoles", "miercoles")
        .replace("SÁBADO", "SABADO")
        .replace("sábado", "sabado")
        .replace("Á", "A")
        .replace("É", "E")
        .replace("Í", "I")
        .replace("Ó", "O")
        .replace("Ú", "U")
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
    )


def effective_pharmacy_weekday(now: datetime | None = None) -> int:
    now = now or datetime.now()
    weekday = now.weekday()
    if now.hour < PHARMACY_SHIFT_CHANGE_HOUR:
        weekday = (weekday - 1) % 7
    return weekday


def is_pharmacy_knowledge_current(knowledge_text: str, now: datetime | None = None) -> bool:
    if not knowledge_text:
        return False

    now = now or datetime.now()
    normalized = _normalize(knowledge_text).lower()

    same_month = re.search(
        r"del\s+(\d{1,2})\s+al\s+(\d{1,2})\s+de\s+([a-z]+)",
        normalized,
        re.IGNORECASE,
    )
    if same_month:
        start_day = int(same_month.group(1))
        end_day = int(same_month.group(2))
        month = MONTHS.get(same_month.group(3))
        if not month:
            return False
        start = date(now.year, month, start_day)
        end = date(now.year, month, end_day)
        return start <= now.date() <= end

    cross_month = re.search(
        r"del\s+(\d{1,2})\s+de\s+([a-z]+)\s+al\s+(\d{1,2})\s+de\s+([a-z]+)",
        normalized,
        re.IGNORECASE,
    )
    if cross_month:
        start_day = int(cross_month.group(1))
        start_month = MONTHS.get(cross_month.group(2))
        end_day = int(cross_month.group(3))
        end_month = MONTHS.get(cross_month.group(4))
        if not start_month or not end_month:
            return False
        start_year = now.year
        end_year = now.year + 1 if end_month < start_month else now.year
        start = date(start_year, start_month, start_day)
        end = date(end_year, end_month, end_day)
        return start <= now.date() <= end

    return False


def extract_pharmacy_for_effective_day(knowledge_text: str, now: datetime | None = None) -> str | None:
    if not knowledge_text:
        return None

    today = DAY_NAMES[effective_pharmacy_weekday(now)]
    normalized = _normalize(knowledge_text)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    day_pattern = re.compile(
        r"^(lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
        r"(?:\s+y\s+(lunes|martes|miercoles|jueves|viernes|sabado|domingo))?"
        r"\s*:\s*(.+)$",
        re.IGNORECASE,
    )

    for line in lines:
        match = day_pattern.match(line)
        if not match:
            continue
        start_day = match.group(1).lower()
        end_day = (match.group(2) or "").lower()
        pharmacy = match.group(3).strip()
        if today == start_day or today == end_day:
            return pharmacy

    return None


def build_pharmacy_response(knowledge_text: str, now: datetime | None = None) -> str | None:
    if not is_pharmacy_knowledge_current(knowledge_text, now):
        return None

    pharmacy = extract_pharmacy_for_effective_day(knowledge_text, now)
    if pharmacy:
        return f"La farmacia de turno ahora es {pharmacy}."
    return None
