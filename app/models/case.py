from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)

    # Tipo de caso
    type = Column(String)
    # infringement | gossip | appeal | report

    status = Column(String, default="pending")
    # pending | in_review | resolved | archived

    priority = Column(Integer, default=3)  # 1 alta, 5 baja

    # Relación con mensaje
    message_id = Column(Integer, ForeignKey("messages.id"))
    message = relationship("Message")

    # Moderador asignado (opcional)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    moderator = relationship("User", foreign_keys=[assigned_to])

    # Info extra
    notes = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
