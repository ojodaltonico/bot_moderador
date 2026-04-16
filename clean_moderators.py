#!/usr/bin/env python3
"""
Script para verificar y limpiar moderadores
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import Moderator

db = SessionLocal()

print("🔍 VERIFICANDO MODERADORES EN LA BASE DE DATOS\n")

# Listar todos los moderadores
mods = db.query(Moderator).all()

print(f"📊 Total de registros: {len(mods)}\n")

for mod in mods:
    print(f"ID: {mod.id}")
    print(f"   Phone: {mod.phone}")
    print(f"   LID: {mod.lid}")
    print(f"   Active: {mod.active}")
    print()

# Preguntar si quiere limpiar
print("\n¿Qué deseas hacer?")
print("1. Eliminar TODOS los moderadores (empezar de cero)")
print("2. Desactivar moderador específico")
print("3. Salir sin cambios")

opcion = input("\nOpción (1/2/3): ").strip()

if opcion == "1":
    confirm = input("⚠️ ¿Estás seguro? Esto eliminará TODOS los moderadores (s/n): ").lower()
    if confirm == 's':
        db.query(Moderator).delete()
        db.commit()
        print("✅ Todos los moderadores han sido eliminados")
    else:
        print("❌ Operación cancelada")

elif opcion == "2":
    phone = input("Ingresa el número a desactivar (ej: 2936417222): ").strip()
    mod = db.query(Moderator).filter(Moderator.phone == phone).first()
    if mod:
        mod.active = False
        db.commit()
        print(f"✅ Moderador {phone} desactivado")
    else:
        print(f"❌ No se encontró moderador con número {phone}")

else:
    print("👋 Saliendo...")

db.close()

print("\n✅ Operación completada")
print("\n📋 Estado final:")
mods = SessionLocal().query(Moderator).filter(Moderator.active == True).all()
print(f"   Moderadores activos: {len(mods)}")
for mod in mods:
    print(f"   - {mod.phone} (LID: {mod.lid or 'sin asignar'})")
