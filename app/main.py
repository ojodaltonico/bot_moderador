from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

# --- DB ---
from app.database import Base, engine
from app.dependencies import get_db

# --- Models ---
from app.models import User, Message, Case, UserAction



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

# =========================
# INGEST MESSAGES
# =========================

@app.post("/ingest_message")
def ingest_message(payload: dict, db: Session = Depends(get_db)):

    phone = payload.get("phone")
    name = payload.get("name")
    chat_id = payload.get("chat_id")
    is_group = payload.get("is_group", True)
    message_type = payload.get("message_type")
    content = payload.get("content")

    if not phone or not message_type:
        return {"error": "invalid payload"}

    # 1️⃣ Buscar o crear usuario
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(phone=phone, name=name)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 2️⃣ Guardar mensaje
    msg = Message(
        user_id=user.id,
        chat_id=chat_id,
        is_group=is_group,
        message_type=message_type,
        content=content
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # 3️⃣ Detección simple (placeholder)
    flagged = False
    if is_group and message_type == "text":
        keywords = ["vendo", "venta", "precio", "promo", "oferta"]
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

    return {
        "stored": True,
        "flagged": flagged,
        "message_id": msg.id
    }

@app.get("/cases/next")
def get_next_case(
    moderator_phone: str,
    db: Session = Depends(get_db)
):
    # 1️⃣ buscar el próximo caso pendiente
    case = (
        db.query(Case)
        .filter(Case.status == "pending")
        .order_by(Case.priority.desc(), Case.id.asc())
        .first()
    )

    if not case:
        return {"message": "no pending cases"}

    # 2️⃣ asignarlo
    case.status = "in_review"
    case.assigned_to = moderator_phone
    db.commit()

    # 3️⃣ traer contexto
    message = db.query(Message).filter(Message.id == case.message_id).first()
    user = db.query(User).filter(User.id == message.user_id).first()

    return {
        "case_id": case.id,
        "type": case.type,
        "priority": case.priority,
        "message": {
            "id": message.id,
            "content": message.content,
            "chat_id": message.chat_id,
        },
        "user": {
            "phone": user.phone,
            "name": user.name,
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
        return {"error": "case not found"}

    if case.status != "in_review":
        return {"error": "case not in review"}

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
            user.status = "warned"


    elif action == "ban":
        user.status = "banned"

    elif action == "delete_message":
        message.deleted = True

    else:
        return {"error": "invalid action"}

    # 2️⃣ cerrar caso
    case.status = "resolved"
    case.resolution = action
    case.resolved_by = moderator_phone
    case.note = note

    # 📜 registrar historial si hay acción disciplinaria
    if action in ["warn", "ban", "delete_message"]:
        log = UserAction(
            user_id=user.id,
            case_id=case.id,
            action=action,
            note=note,
            moderator_phone=moderator_phone
        )
        db.add(log)

    db.commit()

    return {
        "case_id": case.id,
        "status": "resolved",
        "action": action,
        "user": {
            "phone": user.phone,
            "status": user.status
        }
    }

@app.get("/users/{phone}/strikes")
def get_user_strikes(
    phone: str,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone).first()

    if not user:
        return {"error": "user not found"}

    return {
        "phone": user.phone,
        "strikes": user.strikes,
        "status": user.status
    }

@app.get("/users/{phone}/history")
def get_user_history(
    phone: str,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        return {"error": "user not found"}

    cases = (
        db.query(Case)
        .join(Message, Case.message_id == Message.id)
        .filter(Message.user_id == user.id)
        .filter(Case.resolution.in_(["warn", "strike"]))
        .order_by(Case.id.desc())
        .all()
    )

    history = []
    for case in cases:
        history.append({
            "case_id": case.id,
            "action": case.resolution,
            "message": case.message.content,
            "date": case.resolved_at
        })

    return history

@app.post("/appeals")
def create_appeal(
    payload: dict,
    db: Session = Depends(get_db)
):
    phone = payload.get("phone")
    case_id = payload.get("case_id")
    text = payload.get("text")

    if not phone or not case_id or not text:
        return {"error": "invalid payload"}

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        return {"error": "user not found"}

    original_case = db.query(Case).filter(Case.id == case_id).first()
    if not original_case:
        return {"error": "original case not found"}

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

@app.get("/users/{phone}/history")
def user_history(phone: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        return {"error": "user not found"}

    actions = (
        db.query(UserAction)
        .filter(UserAction.user_id == user.id)
        .order_by(UserAction.created_at.desc())
        .all()
    )

    return {
        "user": {
            "phone": user.phone,
            "name": user.name,
            "status": user.status
        },
        "history": [
            {
                "action": a.action,
                "note": a.note,
                "moderator": a.moderator_phone,
                "at": a.created_at
            }
            for a in actions
        ]
    }
