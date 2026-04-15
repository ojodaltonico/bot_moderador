from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import User, Moderator, Case, Message, UserAction
from app.services.groq_chat import ask_groq
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

    def handle_message(self, phone: str, message: str, name: str = "", reply_jid: str | None = None, real_phone: str | None = None):
        """Maneja cualquier mensaje privado"""
        normalized_phone = self.normalize_phone(phone)
        message_lower = message.lower().strip()

        print(f"🤖 Procesando mensaje de {normalized_phone}: {message}")
        print(f"   📞 LID: {phone}")
        print(f"   📱 Real phone: {real_phone}")

        # Verificar si es admin (puede venir como LID o como número real)
        from app.config import ADMIN_PHONE
        is_admin = (phone == str(ADMIN_PHONE)) or (normalized_phone == str(ADMIN_PHONE))
        
        # Si tenemos real_phone, también verificar contra ese
        if real_phone:
            normalized_real = self.normalize_phone(real_phone)
            is_admin = is_admin or (normalized_real == str(ADMIN_PHONE))

        # Verificar si es moderador (ahora busca por LID también)
        is_mod = self._is_moderator(phone)

        print(f"   👑 Admin: {is_admin}, 🛡️ Mod: {is_mod}")

        # Si es moderador y escribe "estoy", actualizar su LID en la BD
        if is_mod and message_lower == "estoy":
            self._update_moderator_lid(phone, real_phone)

        # Comandos de admin
        if is_admin and (message_lower.startswith("agregar mod") or message_lower.startswith("quitar mod")):
            return self._handle_admin_command(normalized_phone, message_lower, reply_jid, real_phone)

        # Comandos de usuario
        if message_lower in ["menu", "/menu"]:
            if is_mod:
                return self._show_moderator_menu(normalized_phone, name, reply_jid)
            return self._show_user_menu(normalized_phone, name, reply_jid)

        if message_lower in ["strikes", "/strikes"]:
            return self._get_user_strikes(normalized_phone, name, reply_jid)

        if message_lower in ["reglas", "/reglas"]:
            return self._get_rules(normalized_phone, reply_jid)

        if message_lower in ["ia", "/ia", "hablar con ia", "hablar con la ia"]:
            return self._show_ai_intro(normalized_phone, name, reply_jid)

        # Sistema de apelación mejorado
        if message_lower in ["apelar", "/apelar"]:
            return self._show_appeal_form(normalized_phone, name, reply_jid)

        # Si el usuario está en proceso de apelación
        if self._is_user_appealing(normalized_phone):
            return self._process_appeal_text(normalized_phone, message, reply_jid)

        # Menú por defecto
        return self._chat_with_ai(normalized_phone, message, reply_jid)

    def _is_moderator(self, phone: str) -> bool:
        """Verifica si es moderador por LID o por número real"""
        mod = self.db.query(Moderator).filter(
            Moderator.active == True,
            (
                (Moderator.lid == phone) |
                (Moderator.phone == phone) |
                (Moderator.phone == self.normalize_phone(phone))
            )
        ).first()
        return mod is not None

    def _update_moderator_lid(self, lid: str, real_phone: str | None):
        """Actualiza el LID de un moderador cuando se identifica"""
        if not real_phone:
            return

        normalized_real = self.normalize_phone(real_phone)

        mod = self.db.query(Moderator).filter(
            Moderator.phone == normalized_real,
            Moderator.active == True
        ).first()

        if mod and not mod.lid:
            mod.lid = lid
            self.db.commit()
            print(f"✅ LID actualizado para moderador {normalized_real}: {lid}")

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

        original_case = self._get_latest_penalty_case(user.id)
        if not original_case:
            return {
                "instructions": {
                    "send_message": True,
                    "to": self._target(phone, reply_jid),
                    "text": "⚠️ No encontré una sanción reciente para asociar la apelación."
                }
            }

        self._mark_user_appealing(user, original_case)

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

        pending_appeals = (
            self.db.query(Case)
            .filter(
                Case.type == "appeal",
                Case.status == "pending",
                Case.note.is_(None),
                Case.created_at > five_min_ago,
                Case.original_case_id.isnot(None)
            )
            .all()
        )
        for appeal in pending_appeals:
            original_case = self.db.query(Case).filter(Case.id == appeal.original_case_id).first()
            if not original_case:
                continue
            original_message = self.db.query(Message).filter(Message.id == original_case.message_id).first()
            if original_message and original_message.user_id == user.id:
                return True
        return False

    def _get_latest_penalty_case(self, user_id: int):
        return (
            self.db.query(Case)
            .join(Message, Case.message_id == Message.id)
            .filter(
                Message.user_id == user_id,
                Case.status == "resolved",
                Case.resolution.in_(["warn", "strike", "ban", "delete", "delete_message", "deleted"])
            )
            .order_by(Case.resolved_at.desc(), Case.id.desc())
            .first()
        )

    def _mark_user_appealing(self, user: User, original_case: Case):
        """Crea una apelación real enlazada al caso sancionatorio original"""
        existing = (
            self.db.query(Case)
            .filter(
                Case.type == "appeal",
                Case.status == "pending",
                Case.note.is_(None),
                Case.original_case_id == original_case.id
            )
            .order_by(Case.created_at.desc())
            .first()
        )
        if existing:
            return existing

        appeal = Case(
            type="appeal",
            status="pending",
            priority=0,
            message_id=original_case.message_id,
            original_case_id=original_case.id,
            note=None
        )
        self.db.add(appeal)
        self.db.commit()
        return appeal

    def _process_appeal_text(self, phone: str, text: str, reply_jid: str | None):
        """Procesa el texto de apelación del usuario"""
        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            return {"error": "Usuario no encontrado"}

        appeal = None
        pending_appeals = (
            self.db.query(Case)
            .filter(
                Case.type == "appeal",
                Case.status == "pending",
                Case.note.is_(None),
                Case.original_case_id.isnot(None)
            )
            .order_by(Case.created_at.desc())
            .all()
        )
        for candidate in pending_appeals:
            original_case = self.db.query(Case).filter(Case.id == candidate.original_case_id).first()
            if not original_case:
                continue
            original_message = self.db.query(Message).filter(Message.id == original_case.message_id).first()
            if original_message and original_message.user_id == user.id:
                appeal = candidate
                break

        if not appeal:
            return self._show_user_menu(phone, user.name, reply_jid)

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

    def _show_ai_intro(self, phone: str, name: str, reply_jid: str | None):
        text = (
            "🗣️ *MODO CHUSMA ACTIVADO*\n\n"
            f"Hola {name or 'usuario'}, mandame lo que quieras por privado y te respondo con la IA.\n\n"
            "Tus palabras clave siguen funcionando igual:\n"
            "• strikes\n"
            "• reglas\n"
            "• apelar\n"
            "• menu\n\n"
            "Escribe menu para volver."
        )

        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": text
            }
        }

    def _chat_with_ai(self, phone: str, message: str, reply_jid: str | None):
        return {
            "instructions": {
                "send_message": True,
                "to": self._target(phone, reply_jid),
                "text": ask_groq(message)
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

    def _handle_admin_command(self, admin_phone: str, command: str, reply_jid: str | None, real_phone: str | None):
        parts = command.split()
        if len(parts) < 3:
            return self._show_admin_help(admin_phone, reply_jid)

        action = parts[0]  # "agregar" o "quitar"
        target_phone = self.normalize_phone(parts[2])  # El número del moderador

        print(f"🔧 Admin command: {action} mod {target_phone}")

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
        """Agrega un moderador y notifica tanto al admin como al nuevo moderador"""
        # Buscar si ya existe
        mod = self.db.query(Moderator).filter(Moderator.phone == target_phone).first()
        
        instructions = []
        
        if not mod:
            # Crear nuevo moderador
            mod = Moderator(phone=target_phone, active=True, lid=None)
            self.db.add(mod)
            self.db.commit()
            print(f"✅ Moderador creado: {target_phone}")
            
            # Mensaje al nuevo moderador
            instructions.append({
                "send_message": True,
                "to": f"{target_phone}@s.whatsapp.net",
                "text": "✅ Has sido agregado como moderador.\n\nEscribe 'estoy' para revisar casos."
            })
            
            # Confirmación al admin
            instructions.append({
                "send_message": True,
                "to": reply_jid,
                "text": f"✅ Moderador agregado: {target_phone}"
            })
            
        else:
            # Reactivar moderador existente
            mod.active = True
            self.db.commit()
            print(f"✅ Moderador reactivado: {target_phone}")
            
            # Mensaje al moderador reactivado
            instructions.append({
                "send_message": True,
                "to": f"{target_phone}@s.whatsapp.net",
                "text": "✅ Tu rol de moderador ha sido reactivado.\n\nEscribe 'estoy' para revisar casos."
            })
            
            # Confirmación al admin
            instructions.append({
                "send_message": True,
                "to": reply_jid,
                "text": f"✅ Moderador reactivado: {target_phone}"
            })

        return {
            "instructions": instructions
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
