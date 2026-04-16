from sqlalchemy import Column, Integer, String, Float, Text
from app.database import Base

class AISettings(Base):
    __tablename__ = "ai_settings"

    id = Column(Integer, primary_key=True, default=1)
    system_prompt = Column(Text, nullable=False, default="")
    temperature = Column(Float, default=0.9)
    max_tokens = Column(Integer, default=400)
    context_window = Column(Integer, default=10)  # cuántos mensajes recordar