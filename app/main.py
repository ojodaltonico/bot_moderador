from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
import os
import json
import re
import secrets
import unicodedata
from app.config import MEDIA_IMAGES_PATH

os.makedirs(MEDIA_IMAGES_PATH, exist_ok=True)

from app.database import Base, engine, ensure_sqlite_schema
from app.dependencies import get_db
from app.models import User, Message, Case, UserAction, Moderator, PendingInstruction, CommunityRequest, Knowledge, ModeratorSession
from app.config import GROUP_ID, ADMIN_PHONE, MEDIA_IMAGES_PATH, PUBLIC_BASE_URL
from app.utils.auth import is_moderator
from app.utils.message_analysis import analyze_message
from app.utils.image_analysis import analyze_image, ocr_image
from app.utils.pharmacy import build_pharmacy_response
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)
ensure_sqlite_schema()

STATUS_ACTIVE = "active"
STATUS_WARNED = "warned"
STATUS_BANNED = "banned"


def _get_case_bundle(db: Session, case: Case):
    message = db.query(Message).filter(Message.id == case.message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="message not found")

    user = db.query(User).filter(User.id == message.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    return message, user


def _get_participant_jid(message: Message) -> str | None:
    participant = message.participant_jid
    if message.whatsapp_message_key:
        try:
            import json
            key_data = json.loads(message.whatsapp_message_key)
            participant = key_data.get("participantAlt") or participant
        except Exception:
            pass
    return participant


def _send_text(to: str, text: str):
    return {"send_message": True, "to": to, "text": text}


def _queue_instructions(db: Session, instructions, source: str = "dashboard"):
    if not instructions:
        return

    instruction_list = instructions if isinstance(instructions, list) else [instructions]
    for instruction in instruction_list:
        db.add(PendingInstruction(
            source=source,
            status="pending",
            payload=json.dumps(instruction)
        ))


def _public_url(path: str) -> str:
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        # Si PUBLIC_BASE_URL no está seteado, intentar obtener la URL pública desde ngrok local
        try:
            import urllib.request, json
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as resp:
                data = json.load(resp)
                for t in data.get("tunnels", []):
                    if t.get("proto") == "https":
                        base = (t.get("public_url") or "").rstrip("/")
                        break
        except Exception:
            base = ""
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _create_moderator_session(db: Session, moderator_phone: str, hours: int = 8) -> ModeratorSession:
    now = datetime.now()
    session = ModeratorSession(
        token=secrets.token_urlsafe(32),
        moderator_phone=str(moderator_phone),
        expires_at=now + timedelta(hours=hours),
        last_seen_at=now
    )
    db.add(session)
    db.flush()
    return session


def _get_moderator_session(db: Session, token: str | None) -> ModeratorSession:
    if not token:
        raise HTTPException(status_code=401, detail="token requerido")

    session = db.query(ModeratorSession).filter(ModeratorSession.token == token).first()
    now = datetime.now()
    if not session or session.revoked_at or session.expires_at < now:
        raise HTTPException(status_code=401, detail="link vencido")

    if not is_moderator(db, session.moderator_phone):
        raise HTTPException(status_code=403, detail="moderador inactivo")

    session.last_seen_at = now
    return session


def _message_preview(msg: Message | None, max_len: int = 220) -> str:
    if not msg:
        return ""
    text = msg.content or msg.media_caption or msg.image_ocr_text or msg.media_filename or msg.message_type or ""
    if len(text) > max_len:
        return text[:max_len - 3] + "..."
    return text


def _serialize_message(db: Session, msg: Message | None, token: str | None = None) -> dict | None:
    if not msg:
        return None
    user = db.query(User).filter(User.id == msg.user_id).first()
    return {
        "id": msg.id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "user_name": user.name if user else None,
        "user_phone": (user.real_phone or user.phone) if user else None,
        "strikes": user.strikes if user else 0,
        "message_type": msg.message_type,
        "content": msg.content,
        "media_caption": msg.media_caption,
        "preview": _message_preview(msg),
        "deleted": msg.deleted,
        "category_label": msg.category_label,
        "intent_label": msg.intent_label,
        "reviewed_category_label": msg.reviewed_category_label,
        "reviewed_intent_label": msg.reviewed_intent_label,
        "effective_category": msg.reviewed_category_label or msg.category_label,
        "effective_intent": msg.reviewed_intent_label or msg.intent_label,
        "image_ocr_text": msg.image_ocr_text,
        "analysis_context": msg.analysis_context,
        "analysis_reason": msg.analysis_reason,
        "analysis_confidence": msg.analysis_confidence,
        "media_url": (
            f"/moderator/media/{msg.media_filename}?token={token}"
            if token and msg.media_filename else None
        )
    }


def _serialize_case(db: Session, case: Case, token: str | None = None) -> dict:
    msg, user = _get_case_bundle(db, case)
    before = (
        db.query(Message)
        .filter(Message.chat_id == msg.chat_id, Message.id < msg.id)
        .order_by(Message.id.desc())
        .limit(5)
        .all()
    )
    after = (
        db.query(Message)
        .filter(Message.chat_id == msg.chat_id, Message.id > msg.id)
        .order_by(Message.id.asc())
        .limit(3)
        .all()
    )
    context = list(reversed(before)) + [msg] + after
    return {
        "id": case.id,
        "type": case.type,
        "status": case.status,
        "priority": case.priority,
        "resolution": case.resolution,
        "assigned_to": case.assigned_to,
        "note": case.note,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "user": {
            "name": user.name,
            "phone": user.real_phone or user.phone,
            "strikes": user.strikes,
            "status": user.status,
        },
        "message": _serialize_message(db, msg, token),
        "context": [_serialize_message(db, item, token) for item in context],
    }


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _normalize_for_topic(text: str | None) -> str:
    text = _strip_accents((text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _jid_from_identifier(identifier: str | None, default_suffix: str = "@s.whatsapp.net") -> str | None:
    if not identifier:
        return None
    identifier = str(identifier)
    if "@" in identifier:
        return identifier
    return f"{identifier}{default_suffix}"


def _reviewer_targets(db: Session) -> list[str]:
    targets = []
    admin_target = _jid_from_identifier(ADMIN_PHONE, "@lid")
    if admin_target:
        targets.append(admin_target)

    moderators = db.query(Moderator).filter(Moderator.active == True).all()
    for moderator in moderators:
        target = _jid_from_identifier(moderator.lid, "@lid") or _jid_from_identifier(moderator.phone)
        if target and target not in targets:
            targets.append(target)

    return targets


def _detect_community_topic(message: Message) -> str | None:
    text = _normalize_for_topic(message.content or message.media_caption)
    if not text:
        return None

    pharmacy_terms = [
        "farmacia de turno", "farmacias de turno", "farmacia turno",
        "farmacia abierta", "que farmacia esta de turno", "q farmacia esta de turno"
    ]
    if "farmacia" in text and ("turno" in text or "abierta" in text):
        return "farmacia_turno"
    if any(term in text for term in pharmacy_terms):
        return "farmacia_turno"

    lost_pet_terms = [
        "perro perdido", "perra perdida", "se perdio mi perro", "se perdio una perra",
        "encontre un perro", "encontre una perra", "perrito perdido", "perrita perdida",
        "mascota perdida", "busco a mi perro"
    ]
    if any(term in text for term in lost_pet_terms):
        return "mascota_perdida"

    return None


def _knowledge_for_topic(db: Session, topic: str) -> Knowledge | None:
    tag_terms = {
        "farmacia_turno": ["farmacia_turno", "farmacia", "turno"],
        "mascota_perdida": ["mascota_perdida", "perro", "mascota"],
    }.get(topic, [topic])

    items = db.query(Knowledge).filter(Knowledge.enabled == True).all()
    for item in items:
        haystack = _normalize_for_topic(f"{item.key} {item.tags}")
        if any(term in haystack for term in tag_terms):
            return item
    return None


def _build_pharmacy_response_from_knowledge(db: Session) -> str | None:
    knowledge = _knowledge_for_topic(db, "farmacia_turno")
    if not knowledge:
        return None
    return build_pharmacy_response(knowledge.content)


def _default_topic_response(topic: str, knowledge: Knowledge | None) -> str | None:
    if topic == "farmacia_turno":
        return None
    if knowledge:
        return knowledge.content
    if topic == "mascota_perdida":
        return (
            "Vi el aviso de mascota perdida. Si pueden, compartan zona, foto, "
            "nombre y un telefono de contacto para que sea mas facil ayudar."
        )
    return None


def _topic_label(topic: str) -> str:
    labels = {
        "farmacia_turno": "farmacia de turno",
        "mascota_perdida": "mascota perdida",
    }
    return labels.get(topic, topic.replace("_", " "))


def _create_community_request_if_needed(db: Session, msg: Message) -> CommunityRequest | None:
    topic = _detect_community_topic(msg)
    if not topic:
        return None

    if topic == "farmacia_turno":
        response_text = _build_pharmacy_response_from_knowledge(db)
        if response_text:
            # Evitar autoreplies repetidos: si el mensaje ya contiene
            # la respuesta sugerida (o viceversa), no publicar de nuevo.
            incoming_text = _normalize_for_topic(msg.content or msg.media_caption)
            response_norm = _normalize_for_topic(response_text)
            if not incoming_text:
                # si no hay texto entrante, no respondemos automáticamente
                return None

            if response_norm in incoming_text or incoming_text in response_norm:
                return None

            # Evitar duplicados en corto plazo (p.ej. 10 minutos)
            recent_similar = (
                db.query(CommunityRequest)
                .filter(
                    CommunityRequest.topic == topic,
                    CommunityRequest.final_response == response_text,
                    CommunityRequest.created_at >= datetime.now() - timedelta(minutes=10)
                )
                .order_by(CommunityRequest.created_at.desc())
                .first()
            )
            if recent_similar:
                return recent_similar

            request = CommunityRequest(
                topic=topic,
                status="answered",
                question_message_id=msg.id,
                suggested_response=response_text,
                final_response=response_text,
                reviewed_by="auto",
                reviewed_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db.add(request)
            db.flush()
            _queue_instructions(db, _send_text(GROUP_ID, response_text), source="community_request")
            return request

    recent_threshold = datetime.now() - timedelta(minutes=45)
    existing = (
        db.query(CommunityRequest)
        .filter(
            CommunityRequest.topic == topic,
            CommunityRequest.status.in_(["pending_review", "pending_answer"]),
            CommunityRequest.created_at >= recent_threshold
        )
        .order_by(CommunityRequest.created_at.desc(), CommunityRequest.id.desc())
        .first()
    )
    if existing:
        return existing

    knowledge = _knowledge_for_topic(db, topic)
    suggested_response = _default_topic_response(topic, knowledge)
    status = "pending_review" if suggested_response else "pending_answer"
    request = CommunityRequest(
        topic=topic,
        status=status,
        question_message_id=msg.id,
        suggested_response=suggested_response,
    )
    db.add(request)
    db.flush()

    preview = (msg.content or msg.media_caption or "").strip()
    if len(preview) > 240:
        preview = preview[:237] + "..."

    label = _topic_label(topic)
    if suggested_response:
        review_text = (
            f"Detecte una pregunta sobre {label} en el grupo.\n\n"
            f"Mensaje:\n{preview}\n\n"
            f"Respuesta sugerida:\n{suggested_response}\n\n"
            f"Responde 1 para publicarla, 2 para ignorar, "
            f"o escribe: resp {request.id} tu respuesta"
        )
    else:
        review_text = (
            f"Detecte una pregunta sobre {label} en el grupo.\n\n"
            f"Mensaje:\n{preview}\n\n"
            f"No tengo una respuesta guardada. Escribe:\n"
            f"resp {request.id} texto de la respuesta\n\n"
            f"Tambien puedes responder 2 para ignorar."
        )

    targets = _reviewer_targets(db)
    request.assigned_to = targets[0] if targets else None
    _queue_instructions(
        db,
        [_send_text(target, review_text) for target in targets],
        source="community_request"
    )
    return request


def _attach_possible_community_answer(db: Session, msg: Message) -> CommunityRequest | None:
    if msg.message_type not in {"text", "image"}:
        return None

    recent_threshold = datetime.now() - timedelta(minutes=45)
    request = (
        db.query(CommunityRequest)
        .filter(
            CommunityRequest.topic == "farmacia_turno",
            CommunityRequest.status == "pending_answer",
            CommunityRequest.answer_message_id.is_(None),
            CommunityRequest.question_message_id != msg.id,
            CommunityRequest.created_at >= recent_threshold
        )
        .order_by(CommunityRequest.created_at.desc(), CommunityRequest.id.desc())
        .first()
    )
    if not request:
        return None

    request.answer_message_id = msg.id
    request.updated_at = datetime.now()

    answer_text = (msg.content or msg.media_caption or "").strip()
    targets = _reviewer_targets(db)
    instructions = []

    if msg.message_type == "text" and answer_text:
        request.status = "pending_review"
        request.suggested_response = answer_text
        review_text = (
            f"Posible respuesta para farmacia de turno #{request.id}:\n\n"
            f"{answer_text}\n\n"
            f"Responde 1 para publicarla, 2 para ignorar, "
            f"o escribe: resp {request.id} tu respuesta corregida"
        )
        instructions.extend(_send_text(target, review_text) for target in targets)
    elif msg.message_type == "image" and msg.media_filename:
        review_text = (
            f"Posible respuesta con imagen para farmacia de turno #{request.id}.\n\n"
            f"Te envio la imagen. Si sirve, escribe:\n"
            f"resp {request.id} texto confirmado para publicar\n\n"
            f"O responde 2 para ignorar."
        )
        for target in targets:
            instructions.append(_send_text(target, review_text))
            instructions.append({
                "send_image": True,
                "to": target,
                "image_path": msg.media_filename,
                "caption": f"Posible respuesta #{request.id}"
            })

    if instructions:
        _queue_instructions(db, instructions, source="community_request")
    return request


def _publish_community_response(
        db: Session,
        request: CommunityRequest,
        response_text: str,
        reviewer: str
) -> dict:
    request.status = "answered"
    request.final_response = response_text
    request.reviewed_by = reviewer
    request.reviewed_at = datetime.now()
    request.updated_at = datetime.now()

    instruction = _send_text(GROUP_ID, response_text)
    _queue_instructions(db, instruction, source="community_request")
    return instruction


def _reject_community_request(db: Session, request: CommunityRequest, reviewer: str):
    request.status = "rejected"
    request.reviewed_by = reviewer
    request.reviewed_at = datetime.now()
    request.updated_at = datetime.now()


def _get_pending_community_request(db: Session, reviewer: str | None = None) -> CommunityRequest | None:
    query = (
        db.query(CommunityRequest)
        .filter(CommunityRequest.status.in_(["pending_review", "pending_answer"]))
    )
    if reviewer:
        reviewer_jid = _jid_from_identifier(reviewer, "@lid")
        query = query.filter(
            (CommunityRequest.assigned_to == reviewer) |
            (CommunityRequest.assigned_to == reviewer_jid) |
            (CommunityRequest.assigned_to.is_(None))
        )
    return query.order_by(CommunityRequest.created_at.asc(), CommunityRequest.id.asc()).first()


def _message_preview(message: Message) -> str:
    return (
        message.content
        or message.media_caption
        or message.image_ocr_text
        or message.media_filename
        or message.message_type
        or ""
    )


def _recent_group_context(db: Session, msg: Message, limit: int = 12) -> list[dict]:
    threshold = datetime.now() - timedelta(minutes=45)
    messages = (
        db.query(Message)
        .filter(
            Message.is_group == True,
            Message.chat_id == msg.chat_id,
            Message.id < msg.id,
            Message.created_at >= threshold
        )
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )

    context = []
    for item in reversed(messages):
        context.append({
            "id": item.id,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "message_type": item.message_type,
            "category_label": item.category_label,
            "text": _message_preview(item)[:240]
        })
    return context


def _analyze_group_image(db: Session, msg: Message):
    image_path = os.path.join(MEDIA_IMAGES_PATH, msg.media_filename) if msg.media_filename else ""
    ocr_text = ocr_image(image_path)
    context = _recent_group_context(db, msg)
    image_analysis = analyze_image(msg.media_caption, ocr_text, context)

    msg.image_ocr_text = image_analysis.ocr_text
    msg.analysis_context = image_analysis.context_text
    msg.analysis_reason = image_analysis.reason
    msg.analysis_confidence = image_analysis.confidence
    msg.category_label = image_analysis.category_label
    msg.intent_label = image_analysis.intent_label
    msg.intent_source = "image_heuristic_context_v1"
    msg.content_length = len((msg.media_caption or "") + (ocr_text or "")) or msg.content_length

    return image_analysis


def _log_action(db: Session, user: User, case: Case, action: str, note: str, moderator_phone: str):
    db.add(UserAction(
        user_id=user.id,
        case_id=case.id,
        action=action,
        note=note,
        moderator_phone=str(moderator_phone)
    ))


def _create_appeal_case(
        db: Session,
        original_case: Case,
        note: str | None,
        status: str = "pending"
) -> Case:
    appeal = Case(
        type="appeal",
        status=status,
        priority=0,
        message_id=original_case.message_id,
        original_case_id=original_case.id,
        note=note
    )
    db.add(appeal)
    db.flush()
    return appeal


def _get_appeals_for_case(db: Session, case: Case):
    root_case_id = case.original_case_id or case.id
    return (
        db.query(Case)
        .filter(Case.type == "appeal", Case.original_case_id == root_case_id)
        .order_by(Case.created_at.desc())
        .all()
    )


def _resolve_case(
        db: Session,
        case: Case,
        action: str,
        moderator_phone: str,
        note: str = "",
        notify_moderator_to: str | None = None,
        notify_user: bool = True,
        allow_reinstate: bool = False
):
    message, user = _get_case_bundle(db, case)
    instructions = []
    moderator_phone = str(moderator_phone)

    if case.type == "appeal":
        if action == "reject_appeal":
            case.resolution = "appeal_rejected"
            if notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    f"❌ Apelación rechazada para {user.phone}.\n\nEscribe 'estoy' para siguiente caso."
                ))
            if notify_user:
                instructions.append(_send_text(
                    user.phone,
                    f"❌ Tu apelación fue revisada y rechazada.\n\nStrikes actuales: {user.strikes}/3"
                ))
        elif action in {"accept_appeal", "reinstate"}:
            if user.strikes > 0:
                user.strikes -= 1

            if user.strikes == 0:
                user.status = STATUS_ACTIVE
            elif user.strikes < 3:
                user.status = STATUS_WARNED

            if action == "reinstate" and allow_reinstate:
                case.resolution = "appeal_accepted_reinstated"
            else:
                case.resolution = "appeal_accepted"

            _log_action(
                db,
                user,
                case,
                "strike_removed",
                note or "Apelación aceptada",
                moderator_phone
            )

            if notify_moderator_to:
                suffix = "\n\nEscribe 'estoy' para siguiente caso."
                if action == "reinstate" and allow_reinstate:
                    participant_jid = _get_participant_jid(message)
                    if participant_jid:
                        instructions.append({
                            "add_user": True,
                            "chat_id": GROUP_ID,
                            "participant_jid": participant_jid
                        })
                    instructions.append(_send_text(
                        notify_moderator_to,
                        (
                            f"✅ Apelación aceptada.\n\n"
                            f"{user.phone} ahora tiene {user.strikes} strike(s).\n"
                            f"Estado: {user.status}.{suffix}"
                        )
                    ))
                else:
                    instructions.append(_send_text(
                        notify_moderator_to,
                        f"✅ Apelación aceptada.\n\n{user.phone} ahora tiene {user.strikes} strike(s).{suffix}"
                    ))

            if notify_user:
                instructions.append(_send_text(
                    user.phone,
                    f"✅ Tu apelación fue aceptada.\n\nSe quitó 1 strike. Ahora tienes {user.strikes}/3 strikes."
                ))
        else:
            raise HTTPException(status_code=400, detail="invalid action")
    else:
        if action in {"approve", "ignore"}:
            case.resolution = "ignored" if action == "ignore" else "approve"
            if notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    f"✅ Caso #{case.id} marcado como 'ignorado'.\n\nEscribe 'estoy' para siguiente caso."
                ))
        elif action == "warn":
            user.status = STATUS_WARNED
            case.resolution = "warn"
            _log_action(db, user, case, "warn", note or "Advertencia aplicada", moderator_phone)
            if notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    f"✅ Usuario {user.real_phone or user.phone} advertido.\nEstado actual: {user.status}.\n\nEscribe 'estoy' para siguiente caso."
                ))
        elif action == "strike":
            user.strikes += 1
            user.status = STATUS_BANNED if user.strikes >= 3 else STATUS_WARNED
            case.resolution = "strike"
            _log_action(db, user, case, "strike", note or "Strike aplicado", moderator_phone)
            if notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    f"✅ Strike aplicado.\nUsuario {user.real_phone or user.phone} ahora tiene {user.strikes} strike(s).\n\nEscribe 'estoy' para siguiente caso."
                ))
        elif action in {"delete", "delete_message"}:
            message.deleted = True
            case.resolution = "deleted"
            if action == "delete":
                user.strikes += 1
                user.status = STATUS_BANNED if user.strikes >= 3 else STATUS_WARNED
                _log_action(db, user, case, "strike", note or "Mensaje borrado por infracción", moderator_phone)
            else:
                _log_action(db, user, case, "delete_message", note or "Mensaje borrado", moderator_phone)
            if notify_moderator_to:
                summary = (
                    f"✅ Mensaje borrado.\nUsuario {user.real_phone or user.phone} ahora tiene {user.strikes} strike(s).\n\nEscribe 'estoy' para siguiente caso."
                ) if action == "delete" else (
                    f"✅ Mensaje borrado para {user.real_phone or user.phone}.\n\nEscribe 'estoy' para siguiente caso."
                )
                instructions.append(_send_text(notify_moderator_to, summary))
            if message.whatsapp_message_key:
                instructions.append({
                    "delete_message": True,
                    "message_key": message.whatsapp_message_key
                })
            elif notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    "⚠️ No se pudo borrar automáticamente (falta ID).\nBórralo manualmente del grupo."
                ))
        elif action == "__legacy_warn__":
            message.deleted = True
            user.strikes += 1
            user.status = STATUS_BANNED if user.strikes >= 3 else STATUS_WARNED

            resolution = "deleted" if action in {"delete", "delete_message"} else action
            log_action = "strike" if action in {"delete", "delete_message"} else action
            log_note = note or "Mensaje borrado por infracción"
            case.resolution = resolution
            _log_action(db, user, case, log_action, log_note, moderator_phone)

            if notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    (
                        f"✅ Mensaje borrado.\n"
                        f"Usuario {user.real_phone or user.phone} ahora tiene {user.strikes} strike(s).\n\n"
                        f"Escribe 'estoy' para siguiente caso."
                    )
                ))

            if message.whatsapp_message_key:
                instructions.append({
                    "delete_message": True,
                    "message_key": message.whatsapp_message_key
                })
            elif notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    "⚠️ No se pudo borrar automáticamente (falta ID).\nBórralo manualmente del grupo."
                ))
        elif action == "ban":
            if user.strikes < 2:
                raise HTTPException(status_code=400, detail="Usuario no tiene strikes suficientes")

            user.strikes += 1
            user.status = STATUS_BANNED
            message.deleted = True
            case.resolution = "banned"
            _log_action(db, user, case, "ban", note or "Expulsado del grupo (3er strike)", moderator_phone)

            if notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    (
                        f"✅ Usuario {user.real_phone or user.phone} expulsado (3er strike).\n\n"
                        f"Escribe 'estoy' para siguiente caso."
                    )
                ))

            if message.whatsapp_message_key:
                instructions.append({
                    "delete_message": True,
                    "message_key": message.whatsapp_message_key
                })

            participant_jid = _get_participant_jid(message)
            if participant_jid:
                instructions.append({
                    "remove_user": True,
                    "chat_id": message.chat_id,
                    "participant_jid": participant_jid
                })
            elif notify_moderator_to:
                instructions.append(_send_text(
                    notify_moderator_to,
                    "⚠️ No se pudo expulsar automáticamente (participant_jid faltante)."
                ))
        else:
            raise HTTPException(status_code=400, detail="invalid action")

    case.status = "resolved"
    case.resolved_by = moderator_phone
    case.resolved_at = datetime.now()
    case.note = note

    return {
        "instructions": instructions,
        "user": user,
        "message": message,
        "case": case
    }


@app.get("/ping")
def ping():
    return {"status": "ok"}


@app.post("/users")
def create_user(
        phone: str,
        name: str | None = None,
        db: Session = Depends(get_db)
):
    user = User(phone=phone, name=name)
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "id": user.id,
        "phone": user.phone,
        "name": user.name,
        "role": user.role,
        "status": user.status,
    }


@app.get("/users")
def list_users(db: Session = Depends(get_db)):
    return db.query(User).all()


@app.get("/users/{phone}/strikes")
def get_user_strikes(
        phone: str,
        db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone).first()

    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    return {
        "phone": user.phone,
        "strikes": user.strikes,
        "status": user.status
    }


@app.get("/users/{phone}/history")
def get_user_history(
        phone: str,
        requester_phone: str,
        db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    is_self = (requester_phone == phone)
    is_mod = is_moderator(db, requester_phone)

    if not is_self and not is_mod:
        raise HTTPException(status_code=403, detail="forbidden")

    actions = (
        db.query(UserAction)
        .filter(UserAction.user_id == user.id)
        .order_by(UserAction.created_at.desc())
        .all()
    )

    history = []
    for action in actions:
        history.append({
            "date": action.created_at.isoformat() if action.created_at else None,
            "action": action.action,
            "case_id": action.case_id,
            "note": action.note or "",
            "moderator": action.moderator_phone
        })

    return {
        "user": {
            "phone": user.phone,
            "name": user.name,
            "status": user.status,
            "strikes": user.strikes
        },
        "history": history
    }


@app.post("/ingest_message")
def ingest_message(payload: dict, db: Session = Depends(get_db)):
    try:
        phone = payload.get("phone")
        real_phone = payload.get("real_phone")
        name = payload.get("name")
        chat_id = payload.get("chat_id")
        is_group = payload.get("is_group", True)
        message_type = payload.get("message_type")
        content = payload.get("content")
        media_caption = payload.get("media_caption")
        whatsapp_message_key = payload.get("whatsapp_message_key")
        participant_jid = payload.get("participant_jid")
        raw_payload = payload.get("raw_payload")

        if not phone or not message_type:
            return {"error": "invalid payload"}

        # Evitar duplicados: si el mensaje ya existe con la misma whatsapp_message_key, retornar
        if whatsapp_message_key:
            existing_msg = db.query(Message).filter(Message.whatsapp_message_key == whatsapp_message_key).first()
            if existing_msg:
                return {
                    "stored": False,
                    "duplicate": True,
                    "message_id": existing_msg.id,
                    "flagged": existing_msg.flagged
                }

        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(phone=phone, real_phone=real_phone, name=name)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            if real_phone and user.real_phone != real_phone:
                user.real_phone = real_phone
                db.commit()

        analysis = analyze_message(
            message_type=message_type,
            content=content if message_type == "text" else None,
            media_caption=media_caption
        )

        msg = Message(
            user_id=user.id,
            chat_id=chat_id,
            is_group=is_group,
            message_type=message_type,
            content=content if message_type == "text" else None,
            media_caption=media_caption,
            media_filename=content if message_type == "image" else None,
            whatsapp_message_key=whatsapp_message_key,
            raw_payload=raw_payload,
            participant_jid=participant_jid,
            category_label=analysis["category_label"],
            intent_label=analysis["intent_label"],
            intent_source=analysis["intent_source"],
            contains_question=analysis["contains_question"],
            contains_link=analysis["contains_link"],
            content_length=analysis["content_length"]
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)

        community_request = None
        possible_community_answer = None
        if is_group and chat_id == GROUP_ID:
            community_request = _create_community_request_if_needed(db, msg)
            if not community_request:
                possible_community_answer = _attach_possible_community_answer(db, msg)
            db.commit()

        if not is_group or chat_id != GROUP_ID:
            return {
                "stored": True,
                "flagged": False,
                "message_id": msg.id
            }

        flagged = False

        if message_type == "text":
            if msg.category_label == "SALE":
                flagged = True
                msg.flagged = True

                case = Case(
                    type="infringement",
                    message_id=msg.id,
                    priority=1
                )
                db.add(case)
                db.commit()

        elif message_type == "image":
            image_analysis = _analyze_group_image(db, msg)
            flagged = image_analysis.should_flag and not possible_community_answer
            msg.flagged = flagged
            if possible_community_answer and image_analysis.should_flag:
                msg.analysis_reason = f"{image_analysis.reason}; posible respuesta comunitaria pendiente"

            if flagged:
                case = Case(
                    type="image_review",
                    message_id=msg.id,
                    priority=image_analysis.priority,
                    note=image_analysis.reason
                )
                db.add(case)
            db.commit()

        return {
            "stored": True,
            "flagged": flagged,
            "message_id": msg.id
        }

    except Exception as e:
        print(f"❌ Error en ingest_message: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


@app.get("/moderation/next")
def get_next_case_for_moderator(
        phone: str,
        db: Session = Depends(get_db)
):
    if not is_moderator(db, phone):
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": "🤖 *Bot Moderador*\n\nOpciones:\n• /strikes - Ver mis advertencias\n• /apelar - Apelar una sanción\n• /reglas - Ver reglas del grupo"
            }
        }

    case = (
        db.query(Case)
        .filter(Case.status == "pending")
        .order_by(
            Case.type == "appeal",
            Case.priority.asc(),
            Case.created_at.asc()
        )
        .first()
    )

    if not case:
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": "✅ No hay casos pendientes. Buen trabajo."
            }
        }

    case.status = "in_review"
    case.assigned_to = phone
    db.commit()

    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    lines = []

    if case.type == "appeal":
        lines.append("📢 *APELACIÓN PENDIENTE*")
        lines.append(f"👤 Usuario: {user.phone}")
        lines.append(f"📝 Motivo: {case.note}")
        lines.append(f"\n🛠️ *Opciones:*")
        lines.append("✅ /aceptar_apelacion - Quitar strike")
        lines.append("❌ /rechazar_apelacion - Mantener sanción")
    else:
        lines.append(f"🚨 *CASO #{case.id}*")
        lines.append(f"👤 {user.name or 'Usuario'} ({user.phone})")
        lines.append(f"⚠️ Strikes acumulados: {user.strikes}")

        if message.message_type == "text":
            lines.append(f"\n💬 *Mensaje:*\n{message.content}")
        elif message.message_type == "image":
            lines.append(f"\n🖼️ *Imagen sospechosa*")
            if message.media_filename:
                lines.append(f"🔗 Ver: http://tudominio.com/media/{message.media_filename}")

        lines.append(f"\n🛠️ *Opciones:*")
        lines.append("✅ /ignorar - No es infracción")
        lines.append("🗑️ /borrar - Eliminar mensaje del grupo")

        if user.strikes >= 2:
            lines.append("🚫 /expulsar - Borrar mensaje y expulsar (3er strike)")

    lines.append(f"\n📝 Uso: /accion {case.id} <opción> [nota]")

    return {
        "instructions": {
            "send_message": True,
            "to": phone,
            "text": "\n".join(lines)
        }
    }


@app.post("/cases/{case_id}/decision")
def decide_case(
        case_id: int,
        payload: dict,
        db: Session = Depends(get_db)
):
    action = payload.get("action")
    moderator_phone = payload.get("moderator_phone")
    note = payload.get("note", "")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    if case.status != "in_review":
        raise HTTPException(status_code=400, detail="case not in review")

    result = _resolve_case(
        db=db,
        case=case,
        action=action,
        moderator_phone=moderator_phone,
        note=note,
        notify_moderator_to=None,
        notify_user=False
    )

    db.commit()

    return {
        "case_id": result["case"].id,
        "status": "resolved",
        "action": result["case"].resolution,
        "user": {
            "phone": result["user"].phone,
            "status": result["user"].status,
            "strikes": result["user"].strikes
        }
    }


@app.get("/cases/{case_id}/history")
def get_case_history(
        case_id: int,
        phone: str,
        db: Session = Depends(get_db)
):
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    actions = (
        db.query(UserAction)
        .filter(UserAction.case_id == case_id)
        .order_by(UserAction.created_at.desc())
        .all()
    )

    appeals = _get_appeals_for_case(db, case)

    return {
        "case": {
            "id": case.id,
            "type": case.type,
            "status": case.status,
            "priority": case.priority,
            "resolution": case.resolution,
            "resolved_by": case.resolved_by,
            "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
            "note": case.note,
            "created_at": case.created_at.isoformat() if case.created_at else None
        },
        "message": {
            "id": message.id,
            "type": message.message_type,
            "content": message.content,
            "media": message.media_filename,
            "deleted": message.deleted
        },
        "user": {
            "phone": user.phone,
            "name": user.name,
            "status": user.status,
            "strikes": user.strikes
        },
        "actions": [
            {
                "action": a.action,
                "note": a.note,
                "moderator": a.moderator_phone,
                "date": a.created_at.isoformat() if a.created_at else None
            }
            for a in actions
        ],
        "appeals": [
            {
                "appeal_id": appeal.id,
                "text": appeal.note,
                "status": appeal.status,
                "created_at": appeal.created_at.isoformat() if appeal.created_at else None
            }
            for appeal in appeals
        ]
    }


@app.post("/appeals")
def create_appeal(
        payload: dict,
        db: Session = Depends(get_db)
):
    phone = payload.get("phone")
    case_id = payload.get("case_id")
    text = payload.get("text")

    if not phone or not case_id or not text:
        raise HTTPException(status_code=400, detail="invalid payload")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    original_case = db.query(Case).filter(Case.id == case_id).first()
    if not original_case:
        raise HTTPException(status_code=404, detail="original case not found")

    message = db.query(Message).filter(Message.id == original_case.message_id).first()
    if message.user_id != user.id:
        raise HTTPException(status_code=403, detail="you can only appeal your own cases")

    appeal = _create_appeal_case(db, original_case, text)
    db.commit()

    return {
        "appeal_created": True,
        "appeal_id": appeal.id
    }


@app.get("/cases/{case_id}/appeals")
def get_case_appeals(
        case_id: int,
        phone: str,
        db: Session = Depends(get_db)
):
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    appeals = _get_appeals_for_case(db, case)

    return {
        "case_id": case_id,
        "appeals": [
            {
                "appeal_id": appeal.id,
                "text": appeal.note,
                "status": appeal.status,
                "created_at": appeal.created_at.isoformat() if appeal.created_at else None
            }
            for appeal in appeals
        ]
    }


@app.get("/media/images/{filename}")
def get_image(
        filename: str,
        phone: str,
        db: Session = Depends(get_db)
):
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    path = os.path.join(MEDIA_IMAGES_PATH, filename)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(path)


@app.get("/moderator", response_class=HTMLResponse)
def moderator_dashboard(token: str, db: Session = Depends(get_db)):
    _get_moderator_session(db, token)
    db.commit()
    return HTMLResponse("""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Moderacion</title>
  <style>
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --text:#17202a; --muted:#657080; --line:#dde3ea; --danger:#b42318; --ok:#166534; --accent:#1f6feb; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--text); }
    header { position:sticky; top:0; z-index:10; background:var(--panel); border-bottom:1px solid var(--line); padding:12px 14px; }
    h1 { margin:0; font-size:18px; }
    .sub { color:var(--muted); font-size:12px; margin-top:2px; }
    main { max-width:820px; margin:0 auto; padding:12px; }
    .tabs { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:10px 0 12px; }
    button, select, textarea { font:inherit; }
    button { border:1px solid var(--line); background:#fff; border-radius:8px; padding:10px 12px; min-height:42px; font-weight:650; }
    button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
    button.danger { background:var(--danger); border-color:var(--danger); color:#fff; }
    button.ok { background:var(--ok); border-color:var(--ok); color:#fff; }
    button:disabled { opacity:.55; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:12px; }
    .row { display:flex; gap:8px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
    .badge { border:1px solid var(--line); border-radius:999px; padding:3px 8px; color:var(--muted); font-size:12px; background:#fafbfc; }
    .meta { color:var(--muted); font-size:12px; line-height:1.35; }
    .text { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.35; margin:9px 0; }
    .media { width:100%; max-height:420px; object-fit:contain; background:#eef1f4; border:1px solid var(--line); border-radius:8px; margin:8px 0; }
    .context { border-top:1px solid var(--line); margin-top:10px; padding-top:8px; }
    .ctx { padding:7px 0; border-bottom:1px solid #edf0f4; }
    .ctx.hit { background:#fff8df; margin:0 -6px; padding:7px 6px; border-radius:6px; }
    .actions { display:grid; grid-template-columns:1fr; gap:8px; margin-top:10px; }
    @media (min-width:620px) { .actions { grid-template-columns:repeat(3, 1fr); } }
    textarea { width:100%; min-height:58px; resize:vertical; border:1px solid var(--line); border-radius:8px; padding:9px; margin-top:8px; }
    .empty { text-align:center; color:var(--muted); padding:28px 10px; }
    .hidden { display:none; }
  </style>
</head>
<body>
  <header>
    <h1>Panel de moderacion</h1>
    <div class="sub" id="status">Cargando...</div>
  </header>
  <main>
    <div class="tabs">
      <button class="primary" id="tabCases" onclick="showTab('cases')">Casos</button>
      <button id="tabHistory" onclick="showTab('history')">Historial</button>
    </div>
    <section id="cases"></section>
    <section id="history" class="hidden"></section>
  </main>
  <script>
    const token = new URLSearchParams(location.search).get('token');
    let activeTab = 'cases';

    function esc(v) {
      return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function time(v) {
      if (!v) return '';
      try { return new Date(v).toLocaleString('es-AR', { dateStyle:'short', timeStyle:'short' }); } catch { return v; }
    }
    async function api(path, options = {}) {
      const sep = path.includes('?') ? '&' : '?';
      const res = await fetch(path + sep + 'token=' + encodeURIComponent(token), options);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }
    function showTab(tab) {
      activeTab = tab;
      document.getElementById('cases').classList.toggle('hidden', tab !== 'cases');
      document.getElementById('history').classList.toggle('hidden', tab !== 'history');
      document.getElementById('tabCases').classList.toggle('primary', tab === 'cases');
      document.getElementById('tabHistory').classList.toggle('primary', tab === 'history');
      refresh();
    }
    function messageHtml(m, hitId) {
      const body = m.content || m.media_caption || m.image_ocr_text || m.preview || '';
      const media = m.media_url ? `<img class="media" src="${esc(m.media_url)}">` : '';
      return `<div class="ctx ${m.id === hitId ? 'hit' : ''}">
        <div class="meta">${esc(time(m.created_at))} · ${esc(m.user_name || '+' + (m.user_phone || ''))} · ${esc(m.effective_category || '-')}</div>
        <div class="text">${esc(body || '[' + (m.message_type || 'mensaje') + ']')}</div>
        ${media}
      </div>`;
    }
    function caseHtml(c) {
      const m = c.message || {};
      const body = m.content || m.media_caption || m.preview || '';
      const reason = m.analysis_reason ? `<div class="meta">Analisis: ${esc(m.analysis_reason)} ${m.analysis_confidence ? '(' + m.analysis_confidence + '%)' : ''}</div>` : '';
      const ocr = m.image_ocr_text ? `<div class="meta">OCR</div><div class="text">${esc(m.image_ocr_text)}</div>` : '';
      const media = m.media_url ? `<img class="media" src="${esc(m.media_url)}">` : '';
      const ban = c.user && c.user.strikes >= 2 ? `<button class="danger" onclick="act(${c.id}, 'ban')">Expulsar</button>` : '';
      return `<article class="card">
        <div class="row">
          <strong>Caso #${c.id}</strong>
          <span class="badge">${esc(c.type)} · prioridad ${esc(c.priority)}</span>
        </div>
        <div class="meta">${esc(time(c.created_at))} · ${esc(c.user.name || '+' + c.user.phone)} · strikes ${esc(c.user.strikes)}/3</div>
        <div class="text">${esc(body || '[sin texto]')}</div>
        ${media}${reason}${ocr}
        <textarea id="note_${c.id}" placeholder="Nota opcional"></textarea>
        <div class="actions">
          <button class="ok" onclick="act(${c.id}, 'ignore')">Ignorar</button>
          <button class="danger" onclick="act(${c.id}, 'delete_message')">Borrar sin strike</button>
          <button class="danger" onclick="act(${c.id}, 'delete')">Borrar + strike</button>
          ${ban}
        </div>
        <div class="context">
          <div class="meta">Contexto del chat</div>
          ${(c.context || []).map(x => messageHtml(x, m.id)).join('')}
        </div>
      </article>`;
    }
    async function refresh() {
      try {
        if (activeTab === 'cases') {
          const data = await api('/moderator/api/cases');
          document.getElementById('status').textContent = `${data.cases.length} caso(s) pendiente(s)`;
          document.getElementById('cases').innerHTML = data.cases.length ? data.cases.map(caseHtml).join('') : '<div class="card empty">No hay casos pendientes.</div>';
        } else {
          const data = await api('/moderator/api/history?limit=60');
          document.getElementById('status').textContent = `${data.messages.length} mensajes recientes`;
          document.getElementById('history').innerHTML = data.messages.map(m => `<article class="card">
            <div class="meta">${esc(time(m.created_at))} · ${esc(m.user_name || '+' + (m.user_phone || ''))} · ${esc(m.effective_category || '-')}</div>
            <div class="text">${esc(m.preview || '[sin texto]')}</div>
            ${m.media_url ? `<img class="media" src="${esc(m.media_url)}">` : ''}
            ${m.deleted ? '<span class="badge">borrado</span>' : `<button class="danger" onclick="deleteMsg(${m.id})">Eliminar mensaje</button>`}
          </article>`).join('');
        }
      } catch (err) {
        document.getElementById('status').textContent = 'Link vencido o error de conexion';
      }
    }
    async function act(id, action) {
      const note = document.getElementById('note_' + id)?.value || '';
      await api('/moderator/api/cases/' + id + '/act', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ action, note })
      });
      refresh();
    }
    async function deleteMsg(id) {
      const note = prompt('Motivo opcional') || '';
      await api('/moderator/api/messages/' + id + '/delete', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ note })
      });
      refresh();
    }
    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
""")


@app.get("/moderator/api/cases")
def moderator_api_cases(token: str, db: Session = Depends(get_db)):
    session = _get_moderator_session(db, token)
    cases = (
        db.query(Case)
        .filter(
            Case.status.in_(["pending", "in_review"]),
            (Case.assigned_to.is_(None)) | (Case.assigned_to == session.moderator_phone)
        )
        .order_by(Case.status.desc(), Case.priority.asc(), Case.id.asc())
        .limit(30)
        .all()
    )
    db.commit()
    return {"cases": [_serialize_case(db, case, token) for case in cases]}


@app.post("/moderator/api/cases/{case_id}/act")
def moderator_api_case_action(case_id: int, payload: dict, token: str, db: Session = Depends(get_db)):
    session = _get_moderator_session(db, token)
    action = payload.get("action")
    note = payload.get("note", "")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case or case.status not in {"pending", "in_review"}:
        raise HTTPException(status_code=404, detail="caso no disponible")
    if case.assigned_to and case.assigned_to != session.moderator_phone:
        raise HTTPException(status_code=409, detail="caso asignado a otro moderador")

    case.status = "in_review"
    case.assigned_to = session.moderator_phone
    result = _resolve_case(
        db=db,
        case=case,
        action=action,
        moderator_phone=session.moderator_phone,
        note=note,
        notify_moderator_to=None,
        notify_user=True,
        allow_reinstate=True
    )
    _queue_instructions(db, result["instructions"], source="moderator_dashboard")
    db.commit()
    return {"ok": True}


@app.get("/moderator/api/history")
def moderator_api_history(token: str, limit: int = 60, db: Session = Depends(get_db)):
    _get_moderator_session(db, token)
    limit = max(10, min(limit, 150))
    messages = (
        db.query(Message)
        .filter(Message.is_group == True, Message.chat_id == GROUP_ID)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
        .all()
    )
    db.commit()
    return {"messages": [_serialize_message(db, msg, token) for msg in messages]}


@app.post("/moderator/api/messages/{message_id}/delete")
def moderator_api_delete_message(message_id: int, payload: dict, token: str, db: Session = Depends(get_db)):
    session = _get_moderator_session(db, token)
    note = payload.get("note") or "Borrado manual desde panel movil"
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="mensaje no encontrado")
    user = db.query(User).filter(User.id == msg.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="usuario no encontrado")

    msg.deleted = True
    case = Case(
        type="manual_delete",
        status="resolved",
        priority=1,
        message_id=msg.id,
        assigned_to=session.moderator_phone,
        resolution="delete_message",
        resolved_by=session.moderator_phone,
        resolved_at=datetime.now(),
        note=note
    )
    db.add(case)
    db.flush()
    _log_action(db, user, case, "delete_message", note, session.moderator_phone)
    if msg.whatsapp_message_key:
        _queue_instructions(db, {
            "delete_message": True,
            "message_key": msg.whatsapp_message_key
        }, source="moderator_dashboard")
    db.commit()
    return {"ok": True}


@app.get("/moderator/media/{filename}")
def moderator_media(filename: str, token: str, db: Session = Depends(get_db)):
    _get_moderator_session(db, token)
    db.commit()
    path = os.path.join(MEDIA_IMAGES_PATH, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


@app.post("/moderators/command")
def moderator_command(payload: dict, db: Session = Depends(get_db)):
    sender = payload.get("phone")
    text = payload.get("content", "").lower()

    if sender != ADMIN_PHONE:
        return {"ignored": True}

    parts = text.split()

    if len(parts) != 3 or parts[1] != "mod":
        return {"ignored": True}

    action, _, target_phone = parts

    mod = db.query(Moderator).filter(Moderator.phone == target_phone).first()

    if action == "agregar":
        if not mod:
            mod = Moderator(phone=target_phone)
            db.add(mod)
        else:
            mod.active = True

        db.commit()
        return {"status": "moderator added", "phone": target_phone}

    if action == "quitar":
        if mod:
            mod.active = False
            db.commit()
        return {"status": "moderator removed", "phone": target_phone}

    return {"ignored": True}


@app.post("/moderation/act")
def moderator_action_whatsapp(
        payload: dict,
        db: Session = Depends(get_db)
):
    phone = payload.get("phone")
    case_id = payload.get("case_id")
    action = payload.get("action")
    note = payload.get("note", "")

    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="Solo moderadores")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case or case.status != "in_review" or case.assigned_to != phone:
        raise HTTPException(status_code=400, detail="Caso no asignado")

    result = _resolve_case(
        db=db,
        case=case,
        action=action,
        moderator_phone=phone,
        note=note,
        notify_moderator_to=phone,
        notify_user=True,
        allow_reinstate=True
    )

    db.commit()

    return {"ok": True, "instructions": result["instructions"]}


@app.get("/user/me")
def user_self_service(
        phone: str,
        db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(phone=phone)
        db.add(user)
        db.commit()

    text = f"""🤖 *Bot Moderador del Grupo*

Hola {user.name or 'usuario'}, tengo estas opciones:

• /strikes - Ver tus advertencias ({user.strikes})
• /apelar - Apelar una sanción
• /reglas - Ver reglas del grupo
• /ayuda - Mostrar este mensaje

Escribe el comando que necesites."""

    return {
        "instructions": {
            "send_message": True,
            "to": phone,
            "text": text
        }
    }


@app.get("/user/{phone}/strikes")
def get_user_strikes_whatsapp(
        phone: str,
        requester: str,
        db: Session = Depends(get_db)
):
    if phone != requester:
        return {
            "instructions": {
                "send_message": True,
                "to": requester,
                "text": "❌ Solo puedes consultar tus propios strikes."
            }
        }

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(phone=phone)
        db.add(user)
        db.commit()

    actions = (
        db.query(UserAction)
        .filter(UserAction.user_id == user.id)
        .order_by(UserAction.created_at.desc())
        .limit(5)
        .all()
    )

    lines = [f"⚠️ *Tus advertencias*\n\nStrikes actuales: {user.strikes}/3"]

    if actions:
        lines.append("\n📜 Historial reciente:")
        for act in actions:
            date = act.created_at.strftime("%d/%m") if act.created_at else "???"
            lines.append(f"• {date} - {act.action}: {act.note or 'Sin nota'}")
    else:
        lines.append("\n✅ No tienes advertencias recientes.")

    if user.strikes >= 2:
        lines.append(
            f"\n🚨 *Advertencia:* Con {user.strikes} strikes, la próxima infracción puede resultar en expulsión.")

    lines.append("\n📝 Para apelar: /apelar <ID_caso> <motivo>")

    return {
        "instructions": {
            "send_message": True,
            "to": phone,
            "text": "\n".join(lines)
        }
    }


@app.post("/appeal/simple")
def create_simple_appeal(
        payload: dict,
        db: Session = Depends(get_db)
):
    phone = payload.get("phone")
    case_id = payload.get("case_id")
    text = payload.get("text", "")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        return {"error": "Usuario no encontrado"}

    original_case = db.query(Case).filter(Case.id == case_id).first()
    if not original_case:
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": f"❌ No se encontró el caso #{case_id}."
            }
        }

    message = db.query(Message).filter(Message.id == original_case.message_id).first()
    if message.user_id != user.id:
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": "❌ Solo puedes apelar tus propias sanciones."
            }
        }

    appeal = _create_appeal_case(db, original_case, f"Apelación: {text}")
    db.commit()

    return {
        "instructions": {
            "send_message": True,
            "to": phone,
            "text": f"✅ Apelación registrada (ID: {appeal.id})."
        }
    }


@app.post("/conversation")
def handle_conversation(payload: dict, db: Session = Depends(get_db)):
    phone = payload.get("phone")
    real_phone = payload.get("real_phone")
    message = payload.get("message", "").strip()
    name = payload.get("name", "")
    reply_jid = payload.get("reply_jid")

    if not phone or not message:
        raise HTTPException(status_code=400, detail="Phone and message required")

    if message.lower() == "estoy":
        if not is_moderator(db, phone):
            return {
                "instructions": {
                    "send_message": True,
                    "to": phone,
                    "text": "🤖 *Bot Moderador*\n\nOpciones:\n• strikes - Ver tus advertencias\n• reglas - Ver reglas del grupo"
                }
            }

        if real_phone:
            from app.utils.phone import normalize_phone
            normalized_real = normalize_phone(real_phone)

            mod = db.query(Moderator).filter(
                Moderator.phone == normalized_real,
                Moderator.active == True
            ).first()

            if mod and not mod.lid:
                mod.lid = phone
                db.commit()

        session = _create_moderator_session(db, phone)
        moderator_link = _public_url(f"/moderator?token={session.token}")

        case = (
            db.query(Case)
            .filter(Case.status == "pending")
            .order_by(
                Case.type == "appeal",
                Case.priority.asc(),
                Case.id.asc()
            )
            .first()
        )

        if not case:
            return {
                "instructions": {
                    "send_message": True,
                    "to": phone,
                    "text": (
                        "No hay casos pendientes. Buen trabajo.\n\n"
                        f"Panel movil: {moderator_link}\n"
                        "El link vence en 8 horas."
                    )
                }
            }

        case.status = "in_review"
        case.assigned_to = phone
        db.commit()

        msg = db.query(Message).filter(Message.id == case.message_id).first()
        user = db.query(User).filter(User.id == msg.user_id).first()

        instructions = []

        if case.type == "appeal":
            cases_with_strikes = (
                db.query(Case)
                .join(Message, Case.message_id == Message.id)
                .join(UserAction, UserAction.case_id == Case.id)
                .filter(
                    Message.user_id == user.id,
                    UserAction.action.in_(["strike", "ban", "warn", "deleted"]),
                    Case.status == "resolved"
                )
                .order_by(Case.resolved_at.desc())
                .limit(5)
                .all()
            )

            text = f"📢 *APELACIÓN - CASO #{case.id}*\n\n"
            text += f"👤 Usuario: {user.name or user.phone}\n"
            text += f"⚠️ Strikes actuales: {user.strikes}/3\n\n"
            text += f"📝 *Descargo del usuario:*\n{case.note}\n\n"

            if cases_with_strikes:
                text += "📜 *Mensajes por los que fue penalizado:*\n\n"
                for i, old_case in enumerate(cases_with_strikes, 1):
                    old_msg = db.query(Message).filter(Message.id == old_case.message_id).first()
                    date = old_case.resolved_at.strftime("%d/%m") if old_case.resolved_at else "???"

                    if old_msg.message_type == "text":
                        content = old_msg.content[:60] + "..." if len(old_msg.content) > 60 else old_msg.content
                    elif old_msg.message_type == "image":
                        content = "🖼️ Imagen"
                    else:
                        content = f"{old_msg.message_type}"

                    text += f"{i}. {date} - {content}\n"

            text += "\n🛠️ *¿Qué decides?*\n"
            text += "Responde con el número:\n\n"
            text += "1. ❌ Rechazar apelación\n"
            text += "2. ✅ Aceptar y quitar 1 strike\n"

            if user.status == STATUS_BANNED:
                text += "3. 🔄 Readmitir al grupo (quita 1 strike)\n"

            text += f"\nPanel movil: {moderator_link}\n"
            text += "El link vence en 8 horas.\n"

            instructions.append({
                "send_message": True,
                "to": phone,
                "text": text
            })

        else:
            display_phone = user.real_phone or user.phone

            text = f"🚨 *CASO #{case.id}*\n\n"
            text += f"👤 Usuario: {user.name or 'Sin nombre'}\n"
            text += f"📞 Número: +{display_phone}\n"
            text += f"⚠️ Strikes acumulados: {user.strikes}/3\n\n"

            if msg.message_type == "text":
                text += f"💬 Mensaje:\n{msg.content}\n\n"
            elif msg.message_type == "image":
                text += f"🖼️ *Imagen sospechosa*\n"
                text += f"(La imagen se enviará a continuación)\n\n"

            text += "🛠️ *¿Qué acción tomas?*\n"
            text += "Responde con el número:\n\n"
            text += "1. ✅ Ignorar (no es infracción)\n"
            text += "2. 🗑️ Borrar mensaje + 1 strike\n"

            if user.strikes >= 2:
                text += "3. 🚫 Expulsar (3er strike)\n"

            text += "\nEjemplo: responde '2' para borrar y sumar strike"
            text += f"\n\nPanel movil: {moderator_link}\n"
            text += "El link vence en 8 horas."

            instructions.append({
                "send_message": True,
                "to": phone,
                "text": text
            })

            if msg.media_filename:
                image_path = os.path.join(MEDIA_IMAGES_PATH, msg.media_filename)
                if os.path.exists(image_path):
                    instructions.append({
                        "send_image": True,
                        "to": phone,
                        "image_path": msg.media_filename,
                        "caption": f"🖼️ Imagen del caso #{case.id}\nUsuario: {user.name or user.phone}"
                    })

        return {"instructions": instructions}

    from app.handlers.conversation import ConversationHandler
    handler = ConversationHandler(db)
    result = handler.handle_message(phone, message, name, reply_jid, real_phone)
    return result


@app.post("/moderation/response")
def process_moderator_response(payload: dict, db: Session = Depends(get_db)):
    phone = payload.get("phone")
    response = payload.get("response", "").strip()

    if not phone or not response:
        return {"error": "Missing phone or response"}

    is_mod = is_moderator(db, phone)
    is_admin = str(phone) == str(ADMIN_PHONE)

    if not is_mod and not is_admin:
        return {"error": "Not a moderator"}

    case = (
        db.query(Case)
        .filter(Case.assigned_to == phone, Case.status == "in_review")
        .first()
    )

    if not case:
        community_request = _get_pending_community_request(db, phone)
        if community_request:
            if response == "1" and community_request.suggested_response:
                _publish_community_response(
                    db,
                    community_request,
                    community_request.suggested_response,
                    phone
                )
                db.commit()
                return {
                    "instructions": [_send_text(
                        phone,
                        f"Respuesta publicada en el grupo para #{community_request.id}."
                    )]
                }

            if response == "2":
                _reject_community_request(db, community_request, phone)
                db.commit()
                return {
                    "instructions": [_send_text(
                        phone,
                        f"Solicitud comunitaria #{community_request.id} ignorada."
                    )]
                }

            return {
                "instructions": [_send_text(
                    phone,
                    (
                        "Hay una solicitud comunitaria pendiente, pero falta una respuesta valida.\n\n"
                        f"Usa 1 para publicar la sugerida, 2 para ignorar, "
                        f"o escribe: resp {community_request.id} tu respuesta"
                    )
                )]
            }

        return {
            "instructions": [{
                "send_message": True,
                "to": phone,
                "text": "❌ No tienes ningún caso en revisión.\n\nEscribe 'estoy' para tomar uno nuevo."
            }]
        }

    message, user = _get_case_bundle(db, case)

    if case.type == "appeal":
        action_map = {"1": "reject_appeal", "2": "accept_appeal"}
        if user.status == STATUS_BANNED:
            action_map["3"] = "reinstate"
    else:
        action_map = {"1": "ignore", "2": "delete"}
        if user.strikes >= 2:
            action_map["3"] = "ban"

    action = action_map.get(response)
    if not action:
        if case.type == "appeal":
            text = "❌ Opción no válida para apelación.\n\nOpciones: 1 (rechazar), 2 (aceptar y quitar strike)"
            if user.status == STATUS_BANNED:
                text += "\n3 (readmitir al grupo)"
        else:
            text = "❌ Opción no válida.\n\nOpciones: 1 (ignorar), 2 (borrar+strike), 3 (expulsar, solo si tiene 2+ strikes)"
        return {"instructions": [_send_text(phone, text)]}

    result = _resolve_case(
        db=db,
        case=case,
        action=action,
        moderator_phone=phone,
        note="",
        notify_moderator_to=phone,
        notify_user=True,
        allow_reinstate=True
    )

    db.commit()
    return {"instructions": result["instructions"]}


@app.get("/media/case/{case_id}")
def get_case_media(case_id: int, phone: str, db: Session = Depends(get_db)):
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    message = db.query(Message).filter(Message.id == case.message_id).first()
    if not message or not message.media_filename:
        raise HTTPException(status_code=404, detail="no media for this case")

    path = os.path.join(MEDIA_IMAGES_PATH, message.media_filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(path, media_type="image/jpeg", filename=message.media_filename)

@app.get("/dashboard/cases")
def dashboard_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).order_by(Case.created_at.desc()).limit(100).all()
    result = []
    for c in cases:
        msg = db.query(Message).filter(Message.id == c.message_id).first()
        user = db.query(User).filter(User.id == msg.user_id).first() if msg else None
        effective_category = (msg.reviewed_category_label or msg.category_label) if msg else None
        effective_intent = (msg.reviewed_intent_label or msg.intent_label) if msg else None
        result.append({
            "id": c.id,
            "type": c.type,
            "status": c.status,
            "priority": c.priority,
            "resolution": c.resolution,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "_messageId": msg.id if msg else None,
            "_userPhone": user.real_phone or user.phone if user else None,
            "_userName": user.name if user else None,
            "_strikes": user.strikes if user else 0,
            "_mediaFilename": msg.media_filename if msg else None,
            "_deleted": msg.deleted if msg else False,
            "_categoryLabel": msg.category_label if msg else None,
            "_intentLabel": msg.intent_label if msg else None,
            "_intentSource": msg.intent_source if msg else None,
            "_reviewedCategoryLabel": msg.reviewed_category_label if msg else None,
            "_reviewedIntentLabel": msg.reviewed_intent_label if msg else None,
            "_reviewedBy": msg.reviewed_by if msg else None,
            "_reviewedAt": msg.reviewed_at.isoformat() if msg and msg.reviewed_at else None,
            "_effectiveCategoryLabel": effective_category,
            "_effectiveIntentLabel": effective_intent,
            "_containsQuestion": msg.contains_question if msg else False,
            "_containsLink": msg.contains_link if msg else False,
            "_imageOcrText": msg.image_ocr_text[:300] if msg and msg.image_ocr_text else None,
            "_analysisReason": msg.analysis_reason if msg else None,
            "_analysisConfidence": msg.analysis_confidence if msg else None,
            "_analysisContext": msg.analysis_context[:300] if msg and msg.analysis_context else None,
            "_content": (
                msg.content[:100] if msg and msg.message_type == "text" and msg.content
                else (msg.media_caption[:100] if msg and msg.message_type == "image" and msg.media_caption else "Imagen sospechosa") if msg and msg.message_type == "image"
                else c.note or ""
            )
        })
    return {"cases": result}


@app.get("/dashboard/group_report")
def dashboard_group_report(days: int = 1, limit: int = 40, db: Session = Depends(get_db)):
    days = max(1, min(days, 30))
    limit = max(10, min(limit, 200))

    now = datetime.now()
    period_start = now - timedelta(days=days - 1)
    period_start = period_start.replace(hour=0, minute=0, second=0, microsecond=0)

    group_messages = (
        db.query(Message)
        .filter(
            Message.is_group == True,
            Message.chat_id == GROUP_ID,
            Message.created_at >= period_start
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )

    recent_messages = (
        db.query(Message)
        .filter(
            Message.is_group == True,
            Message.chat_id == GROUP_ID
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
        .all()
    )

    total_users = (
        db.query(func.count(User.id))
        .scalar()
    ) or 0

    active_threshold = 3
    user_counts = {}
    hourly_counts = {}
    category_counts = {}

    for msg in group_messages:
        user_counts[msg.user_id] = user_counts.get(msg.user_id, 0) + 1
        hour_key = msg.created_at.strftime("%H:00") if msg.created_at else "??:00"
        hourly_counts[hour_key] = hourly_counts.get(hour_key, 0) + 1
        effective_category = msg.reviewed_category_label or msg.category_label or "UNCLASSIFIED"
        category_counts[effective_category] = category_counts.get(effective_category, 0) + 1

    active_users = sum(1 for count in user_counts.values() if count >= active_threshold)
    peak_hour = max(hourly_counts.items(), key=lambda item: item[1])[0] if hourly_counts else None

    top_users = []
    if user_counts:
        sorted_user_counts = sorted(user_counts.items(), key=lambda item: item[1], reverse=True)[:5]
        for user_id, count in sorted_user_counts:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                continue
            top_users.append({
                "name": user.name,
                "phone": user.real_phone or user.phone,
                "count": count
            })

    recent_payload = []
    for msg in recent_messages:
        user = db.query(User).filter(User.id == msg.user_id).first()
        preview = (
            msg.content[:140] if msg.content
            else msg.media_caption[:140] if msg.media_caption
            else msg.media_filename or msg.message_type
        )
        recent_payload.append({
            "id": msg.id,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "user_name": user.name if user else None,
            "user_phone": (user.real_phone or user.phone) if user else None,
            "message_type": msg.message_type,
            "preview": preview,
            "deleted": msg.deleted,
            "effective_category": msg.reviewed_category_label or msg.category_label,
            "effective_intent": msg.reviewed_intent_label or msg.intent_label,
            "analysis_reason": msg.analysis_reason,
            "analysis_confidence": msg.analysis_confidence
        })

    return {
        "summary": {
            "days": days,
            "period_start": period_start.isoformat(),
            "total_messages": len(group_messages),
            "total_users": total_users,
            "active_users": len(user_counts),
            "very_active_users": active_users,
            "deleted_messages": sum(1 for msg in group_messages if msg.deleted),
            "questions": sum(1 for msg in group_messages if msg.contains_question),
            "sale_messages": sum(1 for msg in group_messages if (msg.reviewed_category_label or msg.category_label) == "SALE"),
            "media_messages": sum(1 for msg in group_messages if msg.message_type == "image"),
            "peak_hour": peak_hour
        },
        "hourly": [
            {"hour": hour, "count": count}
            for hour, count in sorted(hourly_counts.items())
        ],
        "categories": [
            {"label": label, "count": count}
            for label, count in sorted(category_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "top_users": top_users,
        "recent_messages": recent_payload
    }


@app.get("/dashboard/analytics")
def dashboard_analytics(days: int = 30, db: Session = Depends(get_db)):
    days = max(1, min(days, 365))
    period_start = datetime.now() - timedelta(days=days)

    messages = (
        db.query(Message)
        .filter(Message.created_at >= period_start)
        .all()
    )
    cases = (
        db.query(Case)
        .filter(Case.created_at >= period_start)
        .all()
    )

    ignored_resolutions = {"ignore", "ignored", "approve"}
    penalty_resolutions = {
        "warn", "strike", "delete", "deleted", "delete_message", "banned"
    }

    reviewed_messages = [
        msg for msg in messages
        if msg.reviewed_category_label or msg.reviewed_intent_label
    ]
    reviewed_category_matches = [
        msg for msg in reviewed_messages
        if msg.reviewed_category_label and msg.category_label
        and msg.reviewed_category_label == msg.category_label
    ]
    reviewed_intent_matches = [
        msg for msg in reviewed_messages
        if msg.reviewed_intent_label and msg.intent_label
        and msg.reviewed_intent_label == msg.intent_label
    ]

    def percent(part: int, total: int) -> float | None:
        if not total:
            return None
        return round((part / total) * 100, 1)

    case_type_stats = {}
    for case in cases:
        stats = case_type_stats.setdefault(case.type or "unknown", {
            "total": 0,
            "pending": 0,
            "ignored": 0,
            "penalized": 0,
            "other_resolved": 0,
        })
        stats["total"] += 1

        resolution = case.resolution or ""
        if case.status in {"pending", "in_review"}:
            stats["pending"] += 1
        elif resolution in ignored_resolutions:
            stats["ignored"] += 1
        elif resolution in penalty_resolutions:
            stats["penalized"] += 1
        else:
            stats["other_resolved"] += 1

    for stats in case_type_stats.values():
        decided = stats["ignored"] + stats["penalized"]
        stats["precision_percent"] = percent(stats["penalized"], decided)
        stats["false_positive_percent"] = percent(stats["ignored"], decided)

    category_counts = {}
    for msg in messages:
        label = msg.reviewed_category_label or msg.category_label or "UNCLASSIFIED"
        category_counts[label] = category_counts.get(label, 0) + 1

    low_precision_types = [
        case_type
        for case_type, stats in case_type_stats.items()
        if stats["precision_percent"] is not None and stats["precision_percent"] < 50
    ]

    suggestions = []
    if not reviewed_messages:
        suggestions.append(
            "Revisar manualmente 30-50 mensajes recientes para crear una muestra de verdad y medir la categoria."
        )
    if low_precision_types:
        suggestions.append(
            "Ajustar reglas de deteccion en: " + ", ".join(sorted(low_precision_types)) + "."
        )
    if category_counts.get("QUESTION", 0) or category_counts.get("COMPLAINT", 0):
        suggestions.append(
            "Usar preguntas y quejas frecuentes para alimentar la base de conocimiento de la IA."
        )
    if not suggestions:
        suggestions.append("Seguir revisando muestras semanales para controlar desvio de la clasificacion.")

    return {
        "period": {
            "days": days,
            "period_start": period_start.isoformat(),
        },
        "summary": {
            "messages": len(messages),
            "cases": len(cases),
            "flagged_messages": sum(1 for msg in messages if msg.flagged),
            "deleted_messages": sum(1 for msg in messages if msg.deleted),
            "reviewed_messages": len(reviewed_messages),
            "category_accuracy_percent": percent(len(reviewed_category_matches), len([
                msg for msg in reviewed_messages
                if msg.reviewed_category_label and msg.category_label
            ])),
            "intent_accuracy_percent": percent(len(reviewed_intent_matches), len([
                msg for msg in reviewed_messages
                if msg.reviewed_intent_label and msg.intent_label
            ])),
        },
        "case_types": case_type_stats,
        "categories": [
            {"label": label, "count": count}
            for label, count in sorted(category_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "suggestions": suggestions,
    }


@app.post("/dashboard/backfill_analysis")
def dashboard_backfill_analysis(limit: int = 500, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 5000))
    messages = (
        db.query(Message)
        .filter(Message.category_label.is_(None))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(limit)
        .all()
    )

    for msg in messages:
        analysis = analyze_message(
            message_type=msg.message_type,
            content=msg.content if msg.message_type == "text" else None,
            media_caption=msg.media_caption,
        )
        msg.category_label = analysis["category_label"]
        msg.intent_label = analysis["intent_label"]
        msg.intent_source = analysis["intent_source"] + "_backfill"
        msg.contains_question = analysis["contains_question"]
        msg.contains_link = analysis["contains_link"]
        msg.content_length = analysis["content_length"]

    db.commit()

    remaining = (
        db.query(func.count(Message.id))
        .filter(Message.category_label.is_(None))
        .scalar()
    ) or 0

    return {
        "ok": True,
        "processed": len(messages),
        "remaining": remaining,
    }


@app.get("/dashboard/community_requests")
def dashboard_community_requests(limit: int = 50, db: Session = Depends(get_db)):
    limit = max(10, min(limit, 200))
    requests = (
        db.query(CommunityRequest)
        .order_by(CommunityRequest.created_at.desc(), CommunityRequest.id.desc())
        .limit(limit)
        .all()
    )

    result = []
    for item in requests:
        msg = db.query(Message).filter(Message.id == item.question_message_id).first()
        user = db.query(User).filter(User.id == msg.user_id).first() if msg else None
        result.append({
            "id": item.id,
            "topic": item.topic,
            "status": item.status,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "reviewed_by": item.reviewed_by,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            "assigned_to": item.assigned_to,
            "suggested_response": item.suggested_response,
            "final_response": item.final_response,
            "question": msg.content or msg.media_caption if msg else None,
            "user_name": user.name if user else None,
            "user_phone": user.real_phone or user.phone if user else None,
        })

    return {"community_requests": result}


@app.post("/dashboard/messages/{message_id}/classify")
def dashboard_classify_message(message_id: int, payload: dict, db: Session = Depends(get_db)):
    category_label = payload.get("category_label")
    intent_label = payload.get("intent_label")
    reviewer = str(payload.get("reviewed_by") or ADMIN_PHONE)

    allowed_categories = {
        "SALE", "QUESTION", "CHAT", "MEDIA", "LINK", "COMPLAINT", "GREETING", "GENERAL",
        "LOST_PET", "FOUND_OBJECT", "COMMUNITY_INFO", "JOB_SEARCH"
    }
    allowed_intents = {
        "OFFER", "INFO_REQUEST", "GENERAL", "MEDIA_SHARE", "SHARE_LINK", "COMPLAINT", "SOCIAL",
        "HELP_REQUEST", "FOUND_ITEM", "INFO_SHARE", "JOB_SEARCH"
    }

    if category_label not in allowed_categories:
        raise HTTPException(status_code=400, detail="categoria invalida")
    if intent_label not in allowed_intents:
        raise HTTPException(status_code=400, detail="intencion invalida")

    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="mensaje no encontrado")

    msg.reviewed_category_label = category_label
    msg.reviewed_intent_label = intent_label
    msg.reviewed_by = reviewer
    msg.reviewed_at = datetime.now()
    db.commit()

    return {
        "ok": True,
        "message_id": msg.id,
        "reviewed_category_label": msg.reviewed_category_label,
        "reviewed_intent_label": msg.reviewed_intent_label,
        "reviewed_by": msg.reviewed_by,
        "reviewed_at": msg.reviewed_at.isoformat() if msg.reviewed_at else None
    }


@app.get("/dashboard/moderators")
def dashboard_moderators(db: Session = Depends(get_db)):
    mods = db.query(Moderator).all()
    return {
        "moderators": [
            {"phone": m.phone, "lid": m.lid, "active": m.active}
            for m in mods
        ]
    }


@app.post("/dashboard/decide")
def dashboard_decide(payload: dict, db: Session = Depends(get_db)):
    case_id = payload.get("case_id")
    action  = payload.get("action")
    note    = payload.get("note", "Desde dashboard LAN")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="caso no encontrado")

    result = _resolve_case(
        db=db,
        case=case,
        action=action,
        moderator_phone=str(ADMIN_PHONE),
        note=note,
        notify_moderator_to=None,
        notify_user=True,
        allow_reinstate=True
    )

    _queue_instructions(db, result["instructions"], source="dashboard")
    db.commit()

    return {
        "ok": True,
        "instructions": result["instructions"],
        "user": {
            "phone": result["user"].phone,
            "strikes": result["user"].strikes,
            "status": result["user"].status
        }
    }


@app.get("/connector/instructions")
def connector_list_instructions(limit: int = 20, db: Session = Depends(get_db)):
    items = (
        db.query(PendingInstruction)
        .filter(PendingInstruction.status == "pending")
        .order_by(PendingInstruction.created_at.asc(), PendingInstruction.id.asc())
        .limit(limit)
        .all()
    )

    return {
        "instructions": [
            {
                "id": item.id,
                "payload": json.loads(item.payload),
                "source": item.source,
                "created_at": item.created_at.isoformat() if item.created_at else None
            }
            for item in items
        ]
    }


@app.post("/connector/instructions/{instruction_id}/ack")
def connector_ack_instruction(instruction_id: int, payload: dict, db: Session = Depends(get_db)):
    item = db.query(PendingInstruction).filter(PendingInstruction.id == instruction_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="instruction not found")

    status = payload.get("status", "processed")
    if status not in {"processed", "failed"}:
        raise HTTPException(status_code=400, detail="invalid status")

    item.status = status
    item.error = payload.get("error")
    item.processed_at = datetime.now()
    db.commit()

    return {"ok": True}


from app.models.ai_settings import AISettings
from app.models.knowledge import Knowledge
from app.utils.ai_config import get_ai_config  # para invalidar caché

@app.get("/admin/ai/config")
def get_ai_config_endpoint(db: Session = Depends(get_db)):
    config = db.query(AISettings).filter(AISettings.id == 1).first()
    if not config:
        config = AISettings(id=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return {
        "system_prompt": config.system_prompt,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "context_window": config.context_window
    }

@app.post("/admin/ai/config")
def update_ai_config(payload: dict, db: Session = Depends(get_db)):
    config = db.query(AISettings).filter(AISettings.id == 1).first()
    if not config:
        config = AISettings(id=1)
        db.add(config)
    config.system_prompt = payload.get("system_prompt", config.system_prompt)
    config.temperature = payload.get("temperature", config.temperature)
    config.max_tokens = payload.get("max_tokens", config.max_tokens)
    config.context_window = payload.get("context_window", config.context_window)
    db.commit()
    # Invalidar caché
    import app.utils.ai_config as ai_config
    ai_config._last_fetch = 0
    return {"ok": True}

@app.get("/admin/knowledge")
def list_knowledge(db: Session = Depends(get_db)):
    items = db.query(Knowledge).all()
    return [{"id": k.id, "key": k.key, "content": k.content, "tags": k.tags, "enabled": k.enabled} for k in items]

@app.post("/admin/knowledge")
def create_knowledge(payload: dict, db: Session = Depends(get_db)):
    key = payload["key"]
    k = db.query(Knowledge).filter(Knowledge.key == key).first()
    if not k:
        k = Knowledge(key=key)
        db.add(k)

    k.content = payload["content"]
    k.tags = payload.get("tags", "")
    k.enabled = payload.get("enabled", True)
    db.commit()
    db.refresh(k)
    return {"id": k.id}

@app.put("/admin/knowledge/{kid}")
def update_knowledge(kid: int, payload: dict, db: Session = Depends(get_db)):
    k = db.query(Knowledge).filter(Knowledge.id == kid).first()
    if not k:
        raise HTTPException(status_code=404)
    k.key = payload.get("key", k.key)
    k.content = payload.get("content", k.content)
    k.tags = payload.get("tags", k.tags)
    k.enabled = payload.get("enabled", k.enabled)
    db.commit()
    return {"ok": True}

@app.delete("/admin/knowledge/{kid}")
def delete_knowledge(kid: int, db: Session = Depends(get_db)):
    k = db.query(Knowledge).filter(Knowledge.id == kid).first()
    if k:
        db.delete(k)
        db.commit()
    return {"ok": True}
