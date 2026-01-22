from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import User, Moderator, Case, Message
import re


class ConversationHandler:
    def __init__(self, db: Session):
        self.db = db

    def normalize_phone(self, phone: str) -> str:
        """Normaliza números de teléfono"""
        if not phone:
            return ""
        return re.sub(r'\D', '', phone)

    def handle_message(self, phone: str, message: str, name: str = ""):
        """Maneja cualquier mensaje privado"""
        normalized_phone = self.normalize_phone(phone)
        message_lower = message.lower().strip()

        print(f"🤖 Procesando mensaje de {normalized_phone}: {message}")

        # 1. Verificar si es admin
        from app.config import ADMIN_PHONE
        is_admin = (normalized_phone == str(ADMIN_PHONE))

        # 2. Verificar si es moderador
        is_mod = self._is_moderator(normalized_phone)

        print(f"   👑 Admin: {is_admin}, 🛡️ Mod: {is_mod}")

        # 3. Si es admin, procesar comandos de admin
        if is_admin and (message_lower.startswith("agregar mod") or message_lower.startswith("quitar mod")):
            return self._handle_admin_command(normalized_phone, message_lower)

        # 4. Comandos de usuario
        if message_lower in ["strikes", "/strikes"]:
            return self._get_user_strikes(normalized_phone, name)

        if message_lower in ["reglas", "/reglas"]:
            return self._get_rules(normalized_phone)

        if message_lower.startswith("/apelar"):
            return self._start_appeal(normalized_phone, message)

        # 5. Menú por defecto (diferente para moderadores vs usuarios)
        if is_mod:
            return self._show_moderator_menu(normalized_phone, name)
        else:
            return self._show_user_menu(normalized_phone, name)

    def _is_moderator(self, phone: str) -> bool:
        """Verifica si es moderador"""
        mod = self.db.query(Moderator).filter(
            Moderator.phone == phone,
            Moderator.active == True
        ).first()
        return mod is not None

    def _handle_admin_command(self, admin_phone: str, command: str):
        """Maneja comandos de admin"""
        parts = command.split()
        if len(parts) < 3:
            return self._show_admin_help()

        action = parts[0]  # "agregar" o "quitar"
        target_phone = self.normalize_phone(parts[2])

        if action == "agregar":
            return self._add_moderator(target_phone)
        elif action == "quitar":
            return self._remove_moderator(target_phone)
        else:
            return self._show_admin_help()

    def _get_user_strikes(self, phone: str, name: str):
        """Muestra strikes de usuario"""
        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(phone=phone, name=name)
            self.db.add(user)
            self.db.commit()

        text = f"⚠️ *TUS ADVERTENCIAS*\n\n"
        text += f"Hola {name or 'usuario'},\n\n"
        text += f"Strikes actuales: *{user.strikes}/3*\n\n"

        if user.strikes == 0:
            text += "✅ No tienes strikes. ¡Sigue así!"
        elif user.strikes == 1:
            text += "⚠️ Tienes 1 strike. Ten cuidado con las reglas."
        elif user.strikes == 2:
            text += "🚨 Tienes 2 strikes. ¡Cuidado! El próximo puede ser expulsión."
        else:
            text += "❌ Tienes 3 strikes. Has sido expulsado del grupo."

        text += "\n\nPara apelar: escribe 'apelar' seguido del número de caso y tu explicación."

        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": text
            }
        }

    def _get_rules(self, phone: str):
        """Devuelve reglas"""
        rules = """📜 *REGLAS DEL GRUPO*

1. 🚫 Prohibido vender/comprar cualquier producto.
2. 👥 Respeto entre miembros.
3. 📵 No spam ni enlaces sospechosos.
4. 🖼️ Imágenes inapropiadas serán eliminadas.

⚠️ *Sistema de strikes:*
- 1ra infracción: Advertencia
- 2da infracción: Strike
- 3ra infracción: Expulsión

📝 Para ver tus strikes: escribe 'strikes'
🛡️ Para apelar: escribe 'apelar'"""

        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": rules
            }
        }

    def _show_moderator_menu(self, phone: str, name: str):
        """Menú para moderadores"""
        text = f"🛡️ *PANEL DE MODERACIÓN*\n\n"
        text += f"Hola {name or 'moderador'},\n\n"
        text += "📋 Comandos disponibles:\n\n"
        text += "• 'estoy' - Ver siguiente caso pendiente\n"
        text += "• 'strikes' - Ver tus strikes\n"
        text += "• 'reglas' - Ver reglas del grupo\n\n"
        text += "Cuando estés revisando un caso, responde con el número de la acción (1, 2, 3)."

        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": text
            }
        }

    def _show_user_menu(self, phone: str, name: str):
        """Menú para usuarios normales"""
        text = f"🤖 *BOT MODERADOR*\n\n"
        text += f"Hola {name or 'usuario'},\n\n"
        text += "Puedo ayudarte con:\n\n"
        text += "• 'strikes' - Ver tus advertencias\n"
        text += "• 'reglas' - Ver reglas del grupo\n"
        text += "• 'apelar' - Apelar una sanción\n\n"
        text += "Escribe una de estas palabras para continuar."

        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": text
            }
        }

    def _add_moderator(self, target_phone: str):
        """Agrega moderador"""
        mod = self.db.query(Moderator).filter(Moderator.phone == target_phone).first()
        if not mod:
            mod = Moderator(phone=target_phone, active=True)
            self.db.add(mod)
            self.db.commit()
            return {
                "instructions": {
                    "send_message": True,
                    "to": target_phone,
                    "text": "✅ Has sido agregado como moderador.\n\nEscribe 'estoy' para revisar casos."
                }
            }
        else:
            mod.active = True
            self.db.commit()
            return {
                "instructions": {
                    "send_message": True,
                    "to": target_phone,
                    "text": "✅ Tu rol de moderador ha sido reactivado."
                }
            }

    def _remove_moderator(self, target_phone: str):
        """Remueve moderador"""
        mod = self.db.query(Moderator).filter(Moderator.phone == target_phone).first()
        if mod:
            mod.active = False
            self.db.commit()

        return {
            "instructions": {
                "send_message": True,
                "to": target_phone,
                "text": "❌ Ya no eres moderador."
            }
        }

    def _show_admin_help(self):
        """Ayuda para admin"""
        return {
            "instructions": {
                "send_message": True,
                "to": "admin",
                "text": "👑 *PANEL DE ADMINISTRADOR*\n\nComandos:\n• agregar mod <número>\n• quitar mod <número>\n\nEjemplo: 'agregar mod 69634422268027'"
            }
        }

    def _start_appeal(self, phone: str, message: str):
        """Inicia proceso de apelación"""
        text = "📝 *APELAR SANCIÓN*\n\n"
        text += "Para apelar una sanción, necesito:\n"
        text += "1. El número de caso\n"
        text += "2. Tu explicación\n\n"
        text += "Formato: /apelar <número_caso> <tu explicación>\n\n"
        text += "Ejemplo: /apelar 5 No estaba vendiendo, era una foto personal"

        return {
            "instructions": {
                "send_message": True,
                "to": phone,
                "text": text
            }
        }