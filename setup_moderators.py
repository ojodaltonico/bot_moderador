#!/usr/bin/env python3
import sys
import os

# Agregar el directorio actual al path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal, engine, Base
from app.models import Moderator, User, Message, Case, UserAction

# Crear tablas si no existen
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Agregar moderadores
moderators = [
    "69634422268027",  # Tu número de WhatsApp
    "92936417222",     # Tu número real normalizado
]

print("🔄 Configurando moderadores...")

for phone in moderators:
    # Verificar si ya existe
    existing = db.query(Moderator).filter(Moderator.phone == phone).first()
    if not existing:
        mod = Moderator(phone=phone, active=True)
        db.add(mod)
        print(f"✅ Moderador agregado: {phone}")
    else:
        existing.active = True
        print(f"✅ Moderador activado: {phone}")

db.commit()
db.close()

print("🎯 Moderadores configurados correctamente")
print("\n📋 Lista de moderadores activos:")
db = SessionLocal()
mods = db.query(Moderator).filter(Moderator.active == True).all()
for mod in mods:
    print(f"   • {mod.phone}")
db.close()