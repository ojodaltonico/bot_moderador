from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)

    # Relación con usuario
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User")

    # Contexto
    chat_id = Column(String, index=True)  # id del grupo o privado
    is_group = Column(Boolean, default=True)

    # Contenido
    message_type = Column(String)  # text | image | audio | video | sticker
    content = Column(String, nullable=True)  # texto o caption
    media_path = Column(String, nullable=True)  # opcional, si se guarda

    # Metadatos
    is_deleted = Column(Boolean, default=False)
    flagged = Column(Boolean, default=False)  # marcado como sospechoso

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    media_filename = Column(String, nullable=True)

    flagged = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)
