from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///./bot.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_sqlite_schema():
    if not DATABASE_URL.startswith("sqlite"):
        return

    expected_message_columns = {
        "media_caption": "TEXT",
        "raw_payload": "TEXT",
        "category_label": "VARCHAR",
        "intent_label": "VARCHAR",
        "intent_source": "VARCHAR",
        "contains_question": "BOOLEAN DEFAULT 0",
        "contains_link": "BOOLEAN DEFAULT 0",
        "content_length": "INTEGER",
    }

    with engine.begin() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        if "messages" not in existing_tables:
            return

        existing_columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(messages)"))
        }

        for column_name, column_sql in expected_message_columns.items():
            if column_name in existing_columns:
                continue
            conn.execute(text(f"ALTER TABLE messages ADD COLUMN {column_name} {column_sql}"))


from app.models.conversation import ConversationTurn
from app.models.ai_settings import AISettings
from app.models.knowledge import Knowledge
from app.models.pending_instruction import PendingInstruction
