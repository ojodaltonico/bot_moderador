from app.config import ADMIN_PHONE
from app.models import Moderator

def is_moderator(db, phone: str) -> bool:
    if phone == ADMIN_PHONE:
        return True

    return (
        db.query(Moderator)
        .filter(Moderator.phone == phone, Moderator.active == True)
        .first()
        is not None
    )
