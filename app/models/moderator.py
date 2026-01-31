from sqlalchemy import Column, Integer, String, Boolean
from app.database import Base

class Moderator(Base):
    __tablename__ = "moderators"

    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True, index=True)  # Número real (2954662475)
    lid = Column(String, nullable=True, index=True)  # LID de WhatsApp (9401733800078)
    active = Column(Boolean, default=True)
