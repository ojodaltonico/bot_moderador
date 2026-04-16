#!/usr/bin/env python3
"""
Script de migración para agregar el campo 'lid' a la tabla moderators
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import Column, String, text
from app.database import SessionLocal, engine

def migrate():
    print("🔄 Iniciando migración de base de datos...")
    
    db = SessionLocal()
    
    try:
        # Verificar si la columna ya existe
        result = db.execute(text("PRAGMA table_info(moderators)"))
        columns = [row[1] for row in result.fetchall()]
        
        if 'lid' in columns:
            print("✅ La columna 'lid' ya existe. No se requiere migración.")
            return
        
        # Agregar la columna 'lid'
        print("📝 Agregando columna 'lid' a la tabla moderators...")
        db.execute(text("ALTER TABLE moderators ADD COLUMN lid VARCHAR"))
        db.commit()
        
        print("✅ Migración completada exitosamente")
        print("\n📋 Información:")
        print("   - Campo 'lid' agregado a la tabla moderators")
        print("   - Este campo se llenará automáticamente cuando los moderadores escriban 'estoy'")
        
    except Exception as e:
        print(f"❌ Error durante la migración: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
