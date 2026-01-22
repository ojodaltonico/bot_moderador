from sqlalchemy import Column, Integer, String, Boolean
from app.database import Base

class Moderator(Base):
    __tablename__ = "moderators"

    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True, index=True)
    active = Column(Boolean, default=True)
