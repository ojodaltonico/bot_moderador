from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text
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
    chat_id = Column(String, index=True)
    is_group = Column(Boolean, default=True)

    # Contenido
    message_type = Column(String)
    content = Column(String, nullable=True)

    # WhatsApp message key (completa, en JSON)
    whatsapp_message_key = Column(Text, nullable=True)

    # Quién envió el mensaje en el grupo (JID real)
    participant_jid = Column(String, nullable=True, index=True)

    # Media
    media_filename = Column(String, nullable=True)

    # Metadatos
    flagged = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())