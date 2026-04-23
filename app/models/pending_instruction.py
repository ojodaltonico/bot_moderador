from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func

from app.database import Base


class PendingInstruction(Base):
    __tablename__ = "pending_instructions"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, nullable=False, default="dashboard")
    status = Column(String, nullable=False, default="pending")
    payload = Column(Text, nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)
