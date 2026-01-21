from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base

class UserAction(Base):
    __tablename__ = "user_actions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=True)

    action = Column(String, nullable=False)  # warn | ban | delete_message
    note = Column(String, nullable=True)

    moderator_phone = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
