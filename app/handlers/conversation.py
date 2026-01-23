from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import User, Moderator, Case, Message
import re


class ConversationHandler:
    def __init__(self, db: Session):
        self.db = db

    # =========================
    # UTILIDADES
    # =========================

    def normalize_phone(self, phone: str) -> str:
        """Normaliza números de teléfono"""
        if not phone:
            return ""
        return re.sub(r'\D', '', phone)

    def _target(self, phone: str, reply_jid: str | None):
        """
        Devuelve el JID correcto para responder.
        PRIORIDAD ABSOLUTA: reply_jid (WhatsApp moderno usa LID)
        """
        return reply_jid or f"{phone}@s.whatsapp.net"

    # =========================
    # HANDLER PRINCIPAL
    # =========================

    def handle_message(
        self,
        phone: str,
        message: str,
        name: str = "",
        reply_jid: str | None = None
    ):
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

        # 3. Comandos de admin
        if is_admin and (
            message_lower.startswith("agregar mod")
            or message_lower.startswith("quitar mod")
        ):
            return self._handle_admin_command(
                normalized_phone,
                message_lower,
                reply_jid
            )

        # 4. Comandos de usuario
        if message_lower in ["strikes", "/strikes"]:
            return self._get_user_strikes(normalized_phone, name, reply_jid)

        if message_lower in ["reglas", "/reglas"]:
            return self._get_rules(normalized_phone, reply_jid)

        if message_lower.startswith("/apelar"):
            return self._start_appeal(normalized_phone, message, reply_jid)

        # 5. Menú por defecto
        if is_mod:
            return self._show_moderator_menu(normalized_phone, name, reply_jid)
        else:
            return self._show_user_menu(normalized_phone, name, reply_jid)

    # =========================
    # VERIFICACIONES
    # =========================

    def _is_moderator(self, phone: str) -> bool:
        mod = self.db.query(Moderator).filter(
            Moderator.phone == phone,
            Moderator.active == True
        ).first()
        return mod is not None

    # =========================
    # ADMIN
    # =========================

    def _handle_admin_command(self, admin_phone: str, command: str, reply_jid: str | None):
        parts = command.split()
        if len(parts) < 3:
            return self._show_admin_help(admin_phone, reply_jid)

        action = parts[0]  # agregar | quitar
        target_phone = self.normalize_phone(parts[2])

        if action == "agregar":
            return self._add_moderator(target_phone, reply_jid)
        elif action == "quitar":
            return self._remove_moderator(target_phone, reply_jid)
        else:
            return self._show_admin_help(admin_phone, reply_jid)

    def _show_admin_help(self, admin_phone: str, reply_jid: str | None):
        return {
            "instructions": {
                "send_message": True,
                "to": self._target(admin_phone, reply_jid),
                "text": (
                    "👑 *PANEL DE ADMINISTRADOR*\n\n"
                    "Comandos:\n"
                    "• agregar mod <número>\n"
                    "• quitar mod <número>\n\n"
                    "Ejemplo:\n"
                    "agregar mod 69634422268027"
                )
            }
        }

    # =========================
    # USUARIO
    # =========================

    def _get_user_strikes(self, phone: str, name: str, reply_jid: str | None):
        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(phone=phone, name=name)
            self.db.add(user)
            self.db.commit()

        text = (
            "⚠️ *TUS ADVERTENCIAS*\n\n"
            f"Hola {name or 'usuario'},\n\n"
            f"Strikes actuales: *{user.strikes}/3*\n\n"
        )

        if user.strikes == 0:
            text += "✅ No tienes strikes. ¡Sigue así!"
        elif user.strikes == 1:
            text += "⚠️ Tienes 1 strike. Ten cuidado con las reglas."
        elif user.strikes == 2:
            text += "🚨 Tienes 2 strikes. ¡Cuidado! El próximo puede ser expulsión."
        else:
            text += "❌ Tienes 3 strikes. Has sido expulsado del grupo."

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }

    def _get_rules(self, phone: str, reply_jid: str | None):
        rules = (
            "📜 *REGLAS DEL GRUPO*\n\n"
            "1. 🚫 Prohibido vender/comprar cualquier producto.\n"
            "2. 👥 Respeto entre miembros.\n"
            "3. 📵 No spam ni enlaces sospechosos.\n"
            "4. 🖼️ Imágenes inapropiadas serán eliminadas.\n\n"
            "⚠️ *Sistema de strikes:*\n"
            "- 1ra infracción: Advertencia\n"
            "- 2da infracción: Strike\n"
            "- 3ra infracción: Expulsión\n\n"
            "📝 Escribe 'strikes' para ver tus advertencias"
        )

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": rules
            }
        }

    # =========================
    # MENÚS
    # =========================

    def _show_moderator_menu(self, phone: str, name: str, reply_jid: str | None):
        text = (
            "🛡️ *PANEL DE MODERACIÓN*\n\n"
            f"Hola {name or 'moderador'},\n\n"
            "📋 Comandos disponibles:\n\n"
            "• 'estoy' - Ver siguiente caso pendiente\n"
            "• 'strikes' - Ver tus strikes\n"
            "• 'reglas' - Ver reglas del grupo\n\n"
            "Cuando estés revisando un caso, responde con 1, 2 o 3."
        )

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }

    def _show_user_menu(self, phone: str, name: str, reply_jid: str | None):
        text = (
            "🤖 *BOT MODERADOR*\n\n"
            f"Hola {name or 'usuario'},\n\n"
            "Puedo ayudarte con:\n\n"
            "• 'strikes' - Ver tus advertencias\n"
            "• 'reglas' - Ver reglas del grupo\n"
            "• '/apelar' - Apelar una sanción\n"
        )

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }

    # =========================
    # MODERADORES
    # =========================

    def _add_moderator(self, target_phone: str, reply_jid: str | None):
        mod = self.db.query(Moderator).filter(Moderator.phone == target_phone).first()
        if not mod:
            mod = Moderator(phone=target_phone, active=True)
            self.db.add(mod)
            self.db.commit()
            text = "✅ Has sido agregado como moderador.\n\nEscribe 'estoy' para revisar casos."
        else:
            mod.active = True
            self.db.commit()
            text = "✅ Tu rol de moderador ha sido reactivado."

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(target_phone, reply_jid),
                "text": text
            }
        }

    def _remove_moderator(self, target_phone: str, reply_jid: str | None):
        mod = self.db.query(Moderator).filter(Moderator.phone == target_phone).first()
        if mod:
            mod.active = False
            self.db.commit()

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(target_phone, reply_jid),
                "text": "❌ Ya no eres moderador."
            }
        }

    # =========================
    # APELACIONES
    # =========================

    def _start_appeal(self, phone: str, message: str, reply_jid: str | None):
        text = (
            "📝 *APELAR SANCIÓN*\n\n"
            "Formato:\n"
            "/apelar <número_caso> <tu explicación>\n\n"
            "Ejemplo:\n"
            "/apelar 5 No estaba vendiendo, era una foto personal"
        )

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }
