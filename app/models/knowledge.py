from sqlalchemy import Column, Integer, String, Text, Boolean
from app.database import Base

class Knowledge(Base):
    __tablename__ = "knowledge_base"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, nullable=True)  # coma separado: "carhue,historia,lago"
    enabled = Column(Boolean, default=True)