from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///./bot.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


from app.models.conversation import ConversationTurn
from app.models.ai_settings import AISettings
from app.models.knowledge import Knowledge
from app.models.pending_instruction import PendingInstruction
