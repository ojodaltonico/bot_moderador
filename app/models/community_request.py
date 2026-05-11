from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class CommunityRequest(Base):
    __tablename__ = "community_requests"

    id = Column(Integer, primary_key=True, index=True)
    topic = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending_review", index=True)

    question_message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    question_message = relationship("Message", foreign_keys=[question_message_id])

    answer_message_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    answer_message = relationship("Message", foreign_keys=[answer_message_id])

    suggested_response = Column(Text, nullable=True)
    final_response = Column(Text, nullable=True)

    assigned_to = Column(String, nullable=True, index=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True)
