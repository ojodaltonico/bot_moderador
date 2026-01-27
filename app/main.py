from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import os
from app.config import MEDIA_IMAGES_PATH

# Crear directorio si no existe
os.makedirs(MEDIA_IMAGES_PATH, exist_ok=True)

# --- DB ---
from app.database import Base, engine
from app.dependencies import get_db

# --- Models ---
from app.models import User, Message, Case, UserAction, Moderator

# --- Config ---
from app.config import GROUP_ID, ADMIN_PHONE, MEDIA_IMAGES_PATH

# --- Utils ---
from app.utils.auth import is_moderator

from fastapi.responses import FileResponse

# =========================
# APP INIT
# =========================

app = FastAPI()

# Crear tablas
Base.metadata.create_all(bind=engine)


# =========================
# HEALTH
# =========================

@app.get("/ping")
def ping():
    return {"status": "ok"}


# =========================
# USERS
# =========================

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


# =========================
# 1️⃣ HISTORIAL DEL USUARIO
# =========================

@app.get("/users/{phone}/history")
def get_user_history(
        phone: str,
        requester_phone: str,  # quien consulta
        db: Session = Depends(get_db)
):
    """
    Historial de acciones disciplinarias de un usuario.

    Reglas:
    - Si requester_phone == phone: puede ver su propio historial
    - Si requester_phone es moderador/admin: puede ver cualquier historial
    """

    # Validar que el usuario existe
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    # Verificar permisos
    is_self = (requester_phone == phone)
    is_mod = is_moderator(db, requester_phone)

    if not is_self and not is_mod:
        raise HTTPException(status_code=403, detail="forbidden")

    # Obtener historial de acciones
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


# =========================
# INGEST MESSAGES
# =========================

@app.post("/ingest_message")
def ingest_message(payload: dict, db: Session = Depends(get_db)):
    try:
        phone = payload.get("phone")
        name = payload.get("name")
        chat_id = payload.get("chat_id")
        is_group = payload.get("is_group", True)
        message_type = payload.get("message_type")
        content = payload.get("content")
        whatsapp_message_key = payload.get("whatsapp_message_key")
        participant_jid = payload.get("participant_jid")

        if not phone or not message_type:
            return {"error": "invalid payload"}

        print(f"📥 Ingresando mensaje: {phone} - {message_type} - WhatsApp Key: {whatsapp_message_key}")

        # 🚫 ignorar completamente audio y video
        if message_type in ["audio", "video"]:
            return {"ignored": True}

        # 1️⃣ usuario
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(phone=phone, name=name)
            db.add(user)
            db.commit()
            db.refresh(user)

        # 2️⃣ mensaje
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

        # 🚫 fuera del grupo moderado
        if not is_group or chat_id != GROUP_ID:
            return {
                "stored": True,
                "flagged": False,
                "message_id": msg.id
            }

        flagged = False

        # 3️⃣ texto con keywords
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
                print(f"✅ Caso creado por texto: {case.id}")

        # 4️⃣ imagen → siempre caso
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
            print(f"✅ Caso creado por imagen: {case.id}")

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


# =========================
# CASOS - MODERACIÓN
# =========================

@app.get("/moderation/next")
def get_next_case_for_moderator(
        phone: str,
        db: Session = Depends(get_db)
):
    """
    Comando "estoy" - Muestra un caso al moderador con opciones contextuales.
    """
    if not is_moderator(db, phone):
        # Si no es moderador, se presenta como bot
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": "🤖 *Bot Moderador*\n\nOpciones:\n• /strikes - Ver mis advertencias\n• /apelar - Apelar una sanción\n• /reglas - Ver reglas del grupo"
            }
        }

    # Buscar caso pendiente (prioridad: apelaciones > infracciones > imágenes)
    case = (
        db.query(Case)
        .filter(Case.status == "pending")
        .order_by(
            Case.type == "appeal",  # Apelaciones primero
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

    # Asignar caso
    case.status = "in_review"
    case.assigned_to = phone
    db.commit()

    # Obtener datos
    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    # Construir mensaje
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

        # Solo mostrar expulsión si tiene 2 o más strikes
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

    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    # 1️⃣ aplicar acción lógica
    if action == "approve":
        pass

    elif action == "warn":
        user.status = "warned"

    elif action == "strike":
        user.strikes += 1
        if user.strikes >= 3:
            user.status = "banned"

    elif action == "ban":
        user.status = "banned"

    elif action == "delete_message":
        message.deleted = True

    else:
        raise HTTPException(status_code=400, detail="invalid action")

    # 2️⃣ cerrar caso
    case.status = "resolved"
    case.resolution = action
    case.resolved_by = moderator_phone
    case.resolved_at = datetime.now()
    case.note = note

    # 📜 registrar historial si hay acción disciplinaria
    if action in ["warn", "strike", "ban", "delete_message"]:
        log = UserAction(
            user_id=user.id,
            case_id=case.id,
            action=action,
            note=note,
            moderator_phone=moderator_phone
        )
        db.add(log)

    # 🧹 borrar imagen si existía
    if message.media_filename:
        path = os.path.join(MEDIA_IMAGES_PATH, message.media_filename)
        if os.path.exists(path):
            os.remove(path)

    db.commit()

    return {
        "case_id": case.id,
        "status": "resolved",
        "action": action,
        "user": {
            "phone": user.phone,
            "status": user.status,
            "strikes": user.strikes
        }
    }


# =========================
# 2️⃣ HISTORIAL DEL CASO
# =========================

@app.get("/cases/{case_id}/history")
def get_case_history(
        case_id: int,
        phone: str,  # quien consulta
        db: Session = Depends(get_db)
):
    """
    Historial completo de un caso.
    Solo moderadores pueden ver esto.
    """

    # Verificar permisos
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    # Mensaje original
    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    # Acciones tomadas
    actions = (
        db.query(UserAction)
        .filter(UserAction.case_id == case_id)
        .order_by(UserAction.created_at.desc())
        .all()
    )

    # Apelaciones relacionadas
    appeals = (
        db.query(Case)
        .filter(Case.type == "appeal", Case.message_id == case.message_id)
        .order_by(Case.created_at.desc())
        .all()
    )

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


# =========================
# 3️⃣ APELACIONES
# =========================

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

    # Verificar que el usuario es el afectado
    message = db.query(Message).filter(Message.id == original_case.message_id).first()
    if message.user_id != user.id:
        raise HTTPException(status_code=403, detail="you can only appeal your own cases")

    appeal = Case(
        type="appeal",
        status="pending",
        priority=0,
        message_id=original_case.message_id,
        note=text
    )

    db.add(appeal)
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
    """
    Ver apelaciones de un caso.
    Solo moderadores.
    """

    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    appeals = (
        db.query(Case)
        .filter(Case.type == "appeal", Case.message_id == case.message_id)
        .order_by(Case.created_at.desc())
        .all()
    )

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


# =========================
# MEDIA
# =========================

@app.get("/media/images/{filename}")
def get_image(
        filename: str,
        phone: str,
        db: Session = Depends(get_db)
):
    # 🔐 permiso
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    path = os.path.join(MEDIA_IMAGES_PATH, filename)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(path)


# =========================
# MODERADORES
# =========================

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
    """
    Ejecuta acción del moderador y devuelve instrucciones para WhatsApp.
    """
    phone = payload.get("phone")  # Moderador
    case_id = payload.get("case_id")
    action = payload.get("action")  # ignore, delete, ban, accept_appeal, reject_appeal
    note = payload.get("note", "")

    # Validar
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="Solo moderadores")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case or case.status != "in_review" or case.assigned_to != phone:
        raise HTTPException(status_code=400, detail="Caso no asignado")

    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    instructions = []

    # === APELACIONES ===
    if case.type == "appeal":
        if action == "accept_appeal":
            if user.strikes > 0:
                user.strikes -= 1
                # Registrar acción
                UserAction(
                    user_id=user.id,
                    case_id=case.id,
                    action="strike_removed",
                    note=f"Apelación aceptada: {note}",
                    moderator_phone=phone
                )
                instructions.append({
                    "send_message": True,
                    "to": user.phone,
                    "text": f"✅ Tu apelación fue aceptada. Ahora tienes {user.strikes} strike(s)."
                })
                instructions.append({
                    "send_message": True,
                    "to": phone,
                    "text": f"✅ Apelación aceptada. Se quitó 1 strike a {user.phone}."
                })

        elif action == "reject_appeal":
            instructions.append({
                "send_message": True,
                "to": user.phone,
                "text": f"❌ Tu apelación fue rechazada. Motivo: {note or 'Sin especificar'}"
            })
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"❌ Apelación rechazada."
            })

    # === CASOS NORMALES ===
    else:
        if action == "ignore":
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"✅ Caso #{case_id} marcado como 'no infracción'."
            })

        elif action == "delete":
            instructions.append({
                "delete_message": True,
                "chat_id": message.chat_id,
                "message_key": message.id  # Necesitarás guardar el ID de WhatsApp
            })
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"🗑️ Mensaje borrado del grupo."
            })

        elif action == "ban":
            # Verificar que tenga al menos 2 strikes
            if user.strikes >= 2:
                user.strikes += 1
                user.status = "banned"

                instructions.append({
                    "delete_message": True,
                    "chat_id": message.chat_id,
                    "message_key": message.id
                })
                instructions.append({
                    "remove_user": True,
                    "chat_id": message.chat_id,
                    "user_phone": user.phone
                })
                instructions.append({
                    "send_message": True,
                    "to": message.chat_id,
                    "text": f"🚫 @{user.phone} ha sido expulsado por acumular 3 strikes."
                })
            else:
                raise HTTPException(status_code=400, detail="Usuario no tiene strikes suficientes")

    # Cerrar caso
    case.status = "resolved"
    case.resolution = action
    case.resolved_by = phone
    case.resolved_at = datetime.now()
    case.note = note

    # Limpiar imagen si existe
    if message.media_filename and os.path.exists(f"{MEDIA_IMAGES_PATH}/{message.media_filename}"):
        os.remove(f"{MEDIA_IMAGES_PATH}/{message.media_filename}")

    db.commit()

    return {"ok": True, "instructions": instructions}


# =========================
# INTERFAZ PARA USUARIOS
# =========================

@app.get("/user/me")
def user_self_service(
        phone: str,
        db: Session = Depends(get_db)
):
    """
    Bot se presenta y muestra opciones al usuario.
    """
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
        requester: str,  # Quien pregunta
        db: Session = Depends(get_db)
):
    """
    Usuario consulta sus strikes (solo puede ver los propios).
    """
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

    # Obtener historial
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
    """
    Apelación directa desde WhatsApp.
    Formato: /apelar 123 "No estaba vendiendo"
    """
    phone = payload.get("phone")
    case_id = payload.get("case_id")
    text = payload.get("text", "")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        return {"error": "Usuario no encontrado"}

    # Buscar caso original
    original_case = db.query(Case).filter(Case.id == case_id).first()
    if not original_case:
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": f"❌ No se encontró el caso #{case_id}."
            }
        }

    # Verificar que el usuario es el afectado
    message = db.query(Message).filter(Message.id == original_case.message_id).first()
    if message.user_id != user.id:
        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": "❌ Solo puedes apelar tus propias sanciones."
            }
        }

    # Crear caso de apelación
    appeal = Case(
        type="appeal",
        status="pending",
        priority=0,  # Máxima prioridad
        message_id=original_case.message_id,
        note=f"Apelación: {text}"
    )
    db.add(appeal)
    db.commit()

    return {
        "instructions": {
            "send_message": True,
            "to": phone,
            "text": f"✅ Apelación registrada (ID: {appeal.id}).\nLos moderadores la revisarán pronto."
        }
    }


# =========================
# MANEJO DE CONVERSACIONES
# =========================

from app.handlers.conversation import ConversationHandler


@app.post("/conversation")
def handle_conversation(payload: dict, db: Session = Depends(get_db)):
    """
    Endpoint central para manejar todas las conversaciones privadas.
    WhatsApp envía aquí TODOS los mensajes privados.
    """
    phone = payload.get("phone")
    message = payload.get("message", "").strip()
    name = payload.get("name", "")

    if not phone or not message:
        raise HTTPException(status_code=400, detail="Phone and message required")

    print(f"📨 /conversation - Phone: {phone}, Message: {message}")

    # ============================================
    # COMANDO "ESTOY" - MOSTRAR SIGUIENTE CASO
    # ============================================
    if message.lower() == "estoy":
        print(f"   🔍 Comando 'estoy' detectado")

        if not is_moderator(db, phone):
            print(f"   ❌ No es moderador")
            return {
                "instructions": {
                    "send_message": True,
                    "to": phone,
                    "text": "🤖 *Bot Moderador*\n\nOpciones:\n• strikes - Ver tus advertencias\n• reglas - Ver reglas del grupo"
                }
            }

        print(f"   ✅ Es moderador, buscando casos...")

        # Buscar caso pendiente (prioridad: apelaciones > infracciones > imágenes)
        case = (
            db.query(Case)
            .filter(Case.status == "pending")
            .order_by(
                Case.type == "appeal",  # Apelaciones primero
                Case.priority.asc(),
                Case.id.asc()
            )
            .first()
        )

        if not case:
            print(f"   ℹ️ No hay casos pendientes")
            return {
                "instructions": {
                    "send_message": True,
                    "to": phone,
                    "text": "✅ No hay casos pendientes. Buen trabajo."
                }
            }

        print(f"   📋 Caso encontrado: #{case.id} (tipo: {case.type})")

        # Asignar caso
        case.status = "in_review"
        case.assigned_to = phone
        db.commit()

        # Obtener datos
        msg = db.query(Message).filter(Message.id == case.message_id).first()
        user = db.query(User).filter(User.id == msg.user_id).first()

        instructions = []

        # ===================================
        # CASO DE APELACIÓN
        # ===================================
        if case.type == "appeal":
            print(f"   📢 Es una apelación")

            # Obtener todos los casos/mensajes que causaron strikes
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

            instructions.append({
                "send_message": True,
                "to": phone,
                "text": text
            })

        # ===================================
        # CASO NORMAL (INFRACCIÓN/IMAGEN)
        # ===================================
        else:
            print(f"   🚨 Es un caso normal")

            text = f"🚨 *CASO #{case.id}*\n\n"
            text += f"👤 Usuario: {user.name or user.phone}\n"
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

            # Si hay imagen, enviarla
            if msg.media_filename:
                image_path = os.path.join(MEDIA_IMAGES_PATH, msg.media_filename)
                if os.path.exists(image_path):
                    print(f"   🖼️ Enviando imagen: {msg.media_filename}")
                    instructions.append({
                        "send_image": True,
                        "to": phone,
                        "image_path": msg.media_filename,
                        "caption": f"🖼️ Imagen del caso #{case.id}\nUsuario: {user.name or user.phone}"
                    })
                else:
                    print(f"   ⚠️ Imagen no encontrada: {image_path}")

        print(f"   ✅ Retornando {len(instructions)} instrucciones")
        return {"instructions": instructions}

    # ============================================
    # OTROS MENSAJES - USAR HANDLER
    # ============================================
    print(f"   ℹ️ Usando ConversationHandler para: {message}")
    from app.handlers.conversation import ConversationHandler
    handler = ConversationHandler(db)
    result = handler.handle_message(phone, message, name)
    print(f"   ✅ Handler retornó: {result}")
    return result


@app.post("/moderation/response")
def process_moderator_response(payload: dict, db: Session = Depends(get_db)):
    """Procesa respuesta numérica de moderador (1, 2, 3)"""
    phone = payload.get("phone")
    response = payload.get("response", "").strip()

    if not phone or not response:
        return {"error": "Missing phone or response"}

    # Verificar si es moderador
    if not is_moderator(db, phone):
        return {"error": "Not a moderator"}

    # Buscar caso asignado a este moderador
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

    # Obtener detalles
    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    instructions = []

    # ============================================
    # CASO DE APELACIÓN
    # ============================================
    if case.type == "appeal":
        if response == "1":
            # Rechazar apelación
            case.status = "resolved"
            case.resolution = "appeal_rejected"
            case.resolved_by = phone
            case.resolved_at = datetime.now()

            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"❌ Apelación rechazada para {user.phone}.\n\nEscribe 'estoy' para siguiente caso."
            })

            instructions.append({
                "send_message": True,
                "to": user.phone,
                "text": f"❌ Tu apelación fue revisada y rechazada.\n\nStrikes actuales: {user.strikes}/3"
            })

        elif response == "2":
            # Aceptar apelación - quitar 1 strike
            if user.strikes > 0:
                user.strikes -= 1

                # Si estaba baneado y ahora tiene menos de 3, reactivar
                if user.status == "banned" and user.strikes < 3:
                    user.status = "active"

                # Registrar acción
                log = UserAction(
                    user_id=user.id,
                    case_id=case.id,
                    action="strike_removed",
                    note="Apelación aceptada",
                    moderator_phone=phone
                )
                db.add(log)

            case.status = "resolved"
            case.resolution = "appeal_accepted"
            case.resolved_by = phone
            case.resolved_at = datetime.now()

            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"✅ Apelación aceptada.\n\n{user.phone} ahora tiene {user.strikes} strike(s).\n\nEscribe 'estoy' para siguiente caso."
            })

            instructions.append({
                "send_message": True,
                "to": user.phone,
                "text": f"✅ Tu apelación fue aceptada.\n\nSe quitó 1 strike. Ahora tienes {user.strikes}/3 strikes.\n\n¡Gracias por tu paciencia!"
            })

        else:
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": "❌ Opción no válida para apelación.\n\nOpciones: 1 (rechazar), 2 (aceptar y quitar strike)"
            })
            db.commit()
            return {"instructions": instructions}

    # ============================================
    # CASOS NORMALES (INFRACCIONES)
    # ============================================
    else:
        if response == "1":
            # Ignorar - solo cerrar caso
            case.status = "resolved"
            case.resolution = "ignored"
            case.resolved_by = phone
            case.resolved_at = datetime.now()

            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"✅ Caso #{case.id} marcado como 'ignorado'.\n\nEscribe 'estoy' para siguiente caso."
            })

        elif response == "2":
            # Borrar mensaje y sumar strike
            message.deleted = True
            user.strikes += 1

            # Si llega a 3 strikes, marcar como baneado
            if user.strikes >= 3:
                user.status = "banned"

            # Registrar acción
            log = UserAction(
                user_id=user.id,
                case_id=case.id,
                action="strike",
                note="Mensaje borrado por infracción",
                moderator_phone=phone
            )
            db.add(log)

            case.status = "resolved"
            case.resolution = "deleted"
            case.resolved_by = phone
            case.resolved_at = datetime.now()

            # Confirmar al moderador
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"✅ Mensaje borrado.\nUsuario {user.phone} ahora tiene {user.strikes} strike(s).\n\nEscribe 'estoy' para siguiente caso."
            })

            # Borrar mensaje del grupo (SI tenemos el ID)
            if message.whatsapp_message_key:
                instructions.append({
                    "delete_message": True,
                    "message_key": message.whatsapp_message_key
                })
            else:
                instructions.append({
                    "send_message": True,
                    "to": phone,
                    "text": f"⚠️ No se pudo borrar automáticamente (falta ID).\nBórralo manualmente del grupo."
                })

        elif response == "3" and user.strikes >= 2:
            # Expulsar (3er strike)
            user.strikes += 1
            user.status = "banned"
            case.status = "resolved"
            case.resolution = "banned"
            case.resolved_by = phone
            case.resolved_at = datetime.now()

            # Registrar acción
            log = UserAction(
                user_id=user.id,
                case_id=case.id,
                action="ban",
                note="Expulsado del grupo (3er strike)",
                moderator_phone=phone
            )
            db.add(log)

            # Confirmar al moderador
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": f"✅ Usuario {user.phone} expulsado (3er strike).\n\nEscribe 'estoy' para siguiente caso."
            })

            # Borrar mensaje del grupo
            if message.whatsapp_message_key:
                instructions.append({
                    "delete_message": True,
                    "message_key": message.whatsapp_message_key
                })

            # Expulsar del grupo usando participant_jid
            participant_to_remove = message.participant_jid
            if message.whatsapp_message_key:
                try:
                    import json
                    key_data = json.loads(message.whatsapp_message_key)
                    participant_to_remove = key_data.get("participantAlt") or message.participant_jid
                except:
                    pass

            if participant_to_remove:
                instructions.append({
                    "remove_user": True,
                    "chat_id": message.chat_id,
                    "participant_jid": participant_to_remove
                })
            else:
                instructions.append({
                    "send_message": True,
                    "to": phone,
                    "text": "⚠️ No se pudo expulsar automáticamente (participant_jid faltante)."
                })

        else:
            instructions.append({
                "send_message": True,
                "to": phone,
                "text": "❌ Opción no válida.\n\nOpciones: 1 (ignorar), 2 (borrar+strike), 3 (expulsar, solo si tiene 2+ strikes)"
            })

    db.commit()
    return {"instructions": instructions}


# En app/main.py, agrega este endpoint:

@app.get("/media/case/{case_id}")
def get_case_media(case_id: int, phone: str, db: Session = Depends(get_db)):
    """Sirve la imagen de un caso a moderadores"""
    # Verificar permisos
    if not is_moderator(db, phone):
        raise HTTPException(status_code=403, detail="forbidden")

    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    message = db.query(Message).filter(Message.id == case.message_id).first()
    if not message or not message.media_filename:
        raise HTTPException(status_code=404, detail="no media for this case")

    # Verificar que el archivo existe
    path = os.path.join(MEDIA_IMAGES_PATH, message.media_filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file not found")

    # Devolver la imagen
    return FileResponse(path, media_type="image/jpeg", filename=message.media_filename)