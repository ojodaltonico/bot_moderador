from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)

    role = Column(String, default="user")     # user | moderator | admin
    status = Column(String, default="active") # active | warned | expelled

    strikes = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
