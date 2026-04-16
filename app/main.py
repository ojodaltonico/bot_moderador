from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime
import os
from app.config import MEDIA_IMAGES_PATH

os.makedirs(MEDIA_IMAGES_PATH, exist_ok=True)

from app.database import Base, engine
from app.dependencies import get_db
from app.models import User, Message, Case, UserAction, Moderator
from app.config import GROUP_ID, ADMIN_PHONE, MEDIA_IMAGES_PATH
from app.utils.auth import is_moderator
from fastapi.responses import FileResponse

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

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
        whatsapp_message_key = payload.get("whatsapp_message_key")
        participant_jid = payload.get("participant_jid")

        if not phone or not message_type:
            return {"error": "invalid payload"}

        if message_type in ["audio", "video"]:
            return {"ignored": True}

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

        msg = Message(
            user_id=user.id,
            chat_id=chat_id,
            is_group=is_group,
            message_type=message_type,
            content=content if message_type == "text" else None,
            media_filename=content if message_type == "image" else None,
            whatsapp_message_key=whatsapp_message_key,
            participant_jid=participant_jid
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)

        if not is_group or chat_id != GROUP_ID:
            return {
                "stored": True,
                "flagged": False,
                "message_id": msg.id
            }

        flagged = False

        if message_type == "text":
            keywords = ["vendo", "venta", "precio", "promo", "oferta", "compro", "negocio", "remato", "liquidacion"]
            if content and any(k in content.lower() for k in keywords):
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
            flagged = True
            msg.flagged = True

            case = Case(
                type="image_review",
                message_id=msg.id,
                priority=2
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
                    "text": "✅ No hay casos pendientes. Buen trabajo."
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

    if not is_moderator(db, phone):
        return {"error": "Not a moderator"}

    case = (
        db.query(Case)
        .filter(Case.assigned_to == phone, Case.status == "in_review")
        .first()
    )

    if not case:
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
        result.append({
            "id": c.id,
            "type": c.type,
            "status": c.status,
            "priority": c.priority,
            "resolution": c.resolution,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "_userPhone": user.real_phone or user.phone if user else None,
            "_userName": user.name if user else None,
            "_strikes": user.strikes if user else 0,
            "_mediaFilename": msg.media_filename if msg else None,
            "_content": (
                msg.content[:100] if msg and msg.message_type == "text" and msg.content
                else "Imagen sospechosa" if msg and msg.message_type == "image"
                else c.note or ""
            )
        })
    return {"cases": result}


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
    k = Knowledge(
        key=payload["key"],
        content=payload["content"],
        tags=payload.get("tags", ""),
        enabled=payload.get("enabled", True)
    )
    db.add(k)
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