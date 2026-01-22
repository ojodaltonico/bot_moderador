#!/usr/bin/env python3
# check_db.py
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal, engine, Base
from app.models import Moderator, User, Message, Case, UserAction
from sqlalchemy import inspect

db = SessionLocal()

# Verificar tablas existentes
inspector = inspect(engine)
tables = inspector.get_table_names()
print("📊 TABLAS EN LA BASE DE DATOS:")
for table in tables:
    print(f"   • {table}")

# Contar registros
print("\n👥 USUARIOS:")
users = db.query(User).all()
print(f"   Total: {len(users)}")
for user in users[:5]:  # Mostrar primeros 5
    print(f"   - {user.phone} ({user.name or 'Sin nombre'})")

print("\n🛡️ MODERADORES:")
mods = db.query(Moderator).filter(Moderator.active == True).all()
print(f"   Activos: {len(mods)}")
for mod in mods:
    print(f"   - {mod.phone}")

print("\n📝 MENSAJES:")
messages = db.query(Message).all()
print(f"   Total: {len(messages)}")
flagged = db.query(Message).filter(Message.flagged == True).count()
print(f"   Marcados: {flagged}")

print("\n🚨 CASOS:")
cases = db.query(Case).all()
print(f"   Total: {len(cases)}")
pending = db.query(Case).filter(Case.status == "pending").count()
print(f"   Pendientes: {pending}")

db.close()