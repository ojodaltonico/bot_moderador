from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)

    # Tipo de caso
    type = Column(String)
    # infringement | image_review | appeal | report

    status = Column(String, default="pending")
    # pending | in_review | resolved | archived

    priority = Column(Integer, default=3)  # 1 alta, 5 baja

    # Relación con mensaje
    message_id = Column(Integer, ForeignKey("messages.id"))
    message = relationship("Message")

    # Referencia al caso original (solo para apelaciones)
    original_case_id = Column(Integer, ForeignKey("cases.id"), nullable=True)

    # Moderador asignado (opcional)
    assigned_to = Column(String, nullable=True)

    # Resolución
    resolution = Column(String, nullable=True)
    resolved_by = Column(String, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Info extra
    note = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())