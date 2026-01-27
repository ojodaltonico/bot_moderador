from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import User, Moderator, Case, Message, UserAction
import re


class ConversationHandler:
    def __init__(self, db: Session):
        self.db = db

    def normalize_phone(self, phone: str) -> str:
        """Normaliza números de teléfono"""
        if not phone:
            return ""
        return re.sub(r'\D', '', phone)

    def _target(self, phone: str, reply_jid: str | None):
        """Devuelve el JID correcto para responder"""
        return reply_jid or f"{phone}@s.whatsapp.net"

    def handle_message(self, phone: str, message: str, name: str = "", reply_jid: str | None = None):
        """Maneja cualquier mensaje privado"""
        normalized_phone = self.normalize_phone(phone)
        message_lower = message.lower().strip()

        print(f"🤖 Procesando mensaje de {normalized_phone}: {message}")

        # Verificar si es admin
        from app.config import ADMIN_PHONE
        is_admin = (normalized_phone == str(ADMIN_PHONE))

        # Verificar si es moderador
        is_mod = self._is_moderator(normalized_phone)

        print(f"   👑 Admin: {is_admin}, 🛡️ Mod: {is_mod}")

        # Comandos de admin
        if is_admin and (message_lower.startswith("agregar mod") or message_lower.startswith("quitar mod")):
            return self._handle_admin_command(normalized_phone, message_lower, reply_jid)

        # Comandos de usuario
        if message_lower in ["strikes", "/strikes"]:
            return self._get_user_strikes(normalized_phone, name, reply_jid)

        if message_lower in ["reglas", "/reglas"]:
            return self._get_rules(normalized_phone, reply_jid)

        # Sistema de apelación mejorado
        if message_lower in ["apelar", "/apelar"]:
            return self._show_appeal_form(normalized_phone, name, reply_jid)

        # Si el usuario está en proceso de apelación
        if self._is_user_appealing(normalized_phone):
            return self._process_appeal_text(normalized_phone, message, reply_jid)

        # Menú por defecto
        if is_mod:
            return self._show_moderator_menu(normalized_phone, name, reply_jid)
        else:
            return self._show_user_menu(normalized_phone, name, reply_jid)

    def _is_moderator(self, phone: str) -> bool:
        mod = self.db.query(Moderator).filter(
            Moderator.phone == phone,
            Moderator.active == True
        ).first()
        return mod is not None

    def _show_appeal_form(self, phone: str, name: str, reply_jid: str | None):
        """Muestra el formulario de apelación con el historial del usuario"""
        user = self.db.query(User).filter(User.phone == phone).first()

        if not user or user.strikes == 0:
            return {
                "instructions": {
                    "send_message": True,
                    "to": self._target(phone, reply_jid),
                    "text": "✅ No tienes strikes acumulados. No necesitas apelar."
                }
            }

        # Obtener CASOS que resultaron en strikes para este usuario
        cases_with_strikes = (
            self.db.query(Case)
            .join(Message, Case.message_id == Message.id)
            .join(UserAction, UserAction.case_id == Case.id)
            .filter(
                Message.user_id == user.id,
                UserAction.action.in_(["strike", "ban", "warn", "deleted"]),
                Case.status == "resolved"
            )
            .order_by(Case.resolved_at.desc())
            .limit(5)
            .all()
        )

        text = f"⚠️ *TUS STRIKES*\n\n"
        text += f"Hola {name or 'usuario'},\n\n"
        text += f"Actualmente tienes *{user.strikes} strike(s)*\n\n"

        if cases_with_strikes:
            text += "📜 *Mensajes por los que fuiste penalizado:*\n\n"
            for i, case in enumerate(cases_with_strikes, 1):
                msg = self.db.query(Message).filter(Message.id == case.message_id).first()
                date = case.resolved_at.strftime("%d/%m/%Y") if case.resolved_at else "???"

                # Obtener el contenido del mensaje original
                if msg.message_type == "text":
                    content = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
                elif msg.message_type == "image":
                    content = "🖼️ Imagen inapropiada"
                else:
                    content = f"Mensaje tipo: {msg.message_type}"

                text += f"{i}. 📅 {date}\n"
                text += f"   💬 {content}\n"
                text += f"   ⚠️ Razón: {case.resolution or 'No especificada'}\n\n"

        text += "📝 *¿Deseas apelar?*\n\n"
        text += "Escribe tu descargo explicando por qué consideras que las sanciones no son justas.\n\n"
        text += "Tu apelación será revisada por un moderador.\n\n"
        text += "💡 Tip: Sé claro y respetuoso en tu explicación."

        # Marcar que el usuario está apelando
        self._mark_user_appealing(phone, True)

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }

    def _is_user_appealing(self, phone: str) -> bool:
        """Verifica si el usuario está en proceso de apelación"""
        from datetime import datetime, timedelta
        five_min_ago = datetime.now() - timedelta(minutes=5)

        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            return False

        # Verificar si tiene un caso "appeal" muy reciente que aún no tiene nota
        appeal = (
            self.db.query(Case)
            .join(Message, Case.message_id == Message.id)
            .filter(
                Message.user_id == user.id,
                Case.type == "appeal",
                Case.status == "pending",
                Case.note.is_(None),
                Case.created_at > five_min_ago
            )
            .first()
        )
        return appeal is not None

    def _mark_user_appealing(self, phone: str, appealing: bool):
        """Marca que el usuario está apelando creando un caso temporal"""
        if not appealing:
            return

        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            return

        # Crear un mensaje temporal para el caso de apelación
        temp_msg = Message(
            user_id=user.id,
            chat_id="temp",
            is_group=False,
            message_type="text",
            content="[Apelación iniciada - esperando descargo]"
        )
        self.db.add(temp_msg)
        self.db.commit()
        self.db.refresh(temp_msg)

        # Crear caso de apelación temporal
        appeal = Case(
            type="appeal",
            status="pending",
            priority=0,  # Máxima prioridad
            message_id=temp_msg.id,
            note=None
        )
        self.db.add(appeal)
        self.db.commit()

    def _process_appeal_text(self, phone: str, text: str, reply_jid: str | None):
        """Procesa el texto de apelación del usuario"""
        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            return {"error": "Usuario no encontrado"}

        # Buscar el caso de apelación pendiente
        appeal = (
            self.db.query(Case)
            .join(Message, Case.message_id == Message.id)
            .filter(
                Message.user_id == user.id,
                Case.type == "appeal",
                Case.status == "pending",
                Case.note.is_(None)
            )
            .order_by(Case.created_at.desc())
            .first()
        )

        if not appeal:
            return self._show_user_menu(phone, user.name, reply_jid)

        # Actualizar el caso con el texto del usuario
        appeal.note = text
        self.db.commit()

        confirmation = f"✅ *Apelación registrada*\n\n"
        confirmation += f"Tu descargo ha sido enviado a los moderadores.\n\n"
        confirmation += f"📋 Caso de apelación #{appeal.id}\n"
        confirmation += f"⚖️ Strikes actuales: {user.strikes}\n\n"
        confirmation += "Los moderadores revisarán tu caso pronto."

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": confirmation
            }
        }

    def _get_user_strikes(self, phone: str, name: str, reply_jid: str | None):
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

        text += "\n\nPara apelar, escribe: *apelar*"

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
            "• 'apelar' - Apelar sanciones\n"
        )

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }

    def _handle_admin_command(self, admin_phone: str, command: str, reply_jid: str | None):
        parts = command.split()
        if len(parts) < 3:
            return self._show_admin_help(admin_phone, reply_jid)

        action = parts[0]
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
                "text": "👑 *PANEL DE ADMINISTRADOR*\n\nComandos:\n• agregar mod <número>\n• quitar mod <número>"
            }
        }

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