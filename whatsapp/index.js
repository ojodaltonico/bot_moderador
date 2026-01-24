import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  downloadMediaMessage
} from "@whiskeysockets/baileys";
import axios from "axios";
import qrcode from "qrcode-terminal";
import { Boom } from "@hapi/boom";
import fs from "fs";
import path from "path";

// ================================
// CONFIGURACIÓN
// ================================
const API_BASE_URL = "http://localhost:8000";
const GROUP_ID = "120363406312544822@g.us";
let socketReady = false;


// Palabras clave para detección de ventas
const SALES_KEYWORDS = ["vendo", "venta", "compro", "precio", "promo", "oferta", "negocio", "negociable", "venda", "comprar", "vendiendo"];

// Directorios para medios
const IMAGE_DIR = path.resolve("../media/temp/images");
if (!fs.existsSync(IMAGE_DIR)) {
  fs.mkdirSync(IMAGE_DIR, { recursive: true });
}

// ================================
// FUNCIÓN PARA BORRAR MENSAJES
// ================================
async function deleteMessageFromGroup(sock, messageKey) {
    try {
        console.log(`🗑️ Intentando borrar mensaje...`);
        console.log(`   Key:`, JSON.stringify(messageKey, null, 2));

        await sock.sendMessage(messageKey.remoteJid, {
            delete: messageKey
        });

        console.log(`✅ Mensaje borrado exitosamente`);
        return true;
    } catch (error) {
        console.error(`❌ Error al borrar mensaje: ${error.message}`);
        return false;
    }
}

// ================================
// FUNCIÓN PARA EXPULSAR USUARIOS
// ================================
// En la función removeUserFromGroup (línea ~50)
async function removeUserFromGroup(sock, chatId, participantJid) {
    try {
        console.log(`🚫 Expulsando ${participantJid} del grupo ${chatId}`);

        // Intentar con el JID tal cual viene
        console.log(`   Intentando con: ${participantJid}`);
        await sock.groupParticipantsUpdate(
            chatId,
            [participantJid],
            "remove"
        );

        console.log(`✅ Usuario expulsado exitosamente`);
        return true;
    } catch (error) {
        console.error(`❌ Error expulsando usuario:`, error.message);
        console.error(`   Stack:`, error.stack);

        // Si falla, intentar con formato @s.whatsapp.net
        if (participantJid.includes('@lid')) {
            const phoneNumber = participantJid.split('@')[0];
            const altJid = `${phoneNumber}@s.whatsapp.net`;
            console.log(`   🔄 Reintentando con formato alternativo: ${altJid}`);

            try {
                await sock.groupParticipantsUpdate(
                    chatId,
                    [altJid],
                    "remove"
                );
                console.log(`✅ Usuario expulsado con formato alternativo`);
                return true;
            } catch (error2) {
                console.error(`❌ También falló con formato alternativo:`, error2.message);
                return false;
            }
        }

        return false;
    }
}


// ================================
// FUNCIÓN PARA PROCESAR INSTRUCCIONES
// ================================
async function processInstructions(instructions, sock, originalChatId = null) {
    if (!socketReady) {
        console.log("⏳ Socket no listo. Cancelando procesamiento de instrucciones.");
        return;
    }
    if (!instructions) {
        console.log("⚠️ No hay instrucciones para procesar");
        return;
    }

    console.log("🔧 Procesando instrucciones:", JSON.stringify(instructions, null, 2));

    // Si es un objeto con una sola instrucción, convertir a array
    let instructionList = [];
    if (Array.isArray(instructions)) {
        instructionList = instructions;
    } else if (instructions.send_message || instructions.send_image || instructions.delete_message || instructions.remove_user) {
        instructionList = [instructions];
    } else {
        console.log("⚠️ Formato de instrucciones no reconocido");
        return;
    }

    console.log(`📋 Total de instrucciones a procesar: ${instructionList.length}`);

    for (const instruction of instructionList) {
        try {
            console.log("🔹 Procesando instrucción:", instruction);

            // 1. Enviar mensaje
            if (instruction.send_message && instruction.to && instruction.text) {
                // 🔧 SOLO usar originalChatId si NO es un grupo
                let targetChat = instruction.to;

                // Si el 'to' no incluye '@', es solo un número de teléfono
                // En ese caso, usar el originalChatId que tiene el formato correcto (@lid o @s.whatsapp.net)
                if (originalChatId && !instruction.to.includes('@g.us')) {
                    targetChat = originalChatId;
                }

                console.log(`📤 Enviando mensaje a ${targetChat}`);
                await sock.sendMessage(targetChat, {
                    text: instruction.text
                });
                console.log(`✅ Mensaje enviado a ${targetChat}`);
            }

            // 2. Enviar imagen
            if (instruction.send_image && instruction.to && instruction.image_path) {
                let targetChat = instruction.to;

                if (originalChatId && !instruction.to.includes('@g.us')) {
                    targetChat = originalChatId;
                }

                const imagePath = path.join(IMAGE_DIR, instruction.image_path);

                console.log(`📸 Enviando imagen desde: ${imagePath}`);

                if (fs.existsSync(imagePath)) {
                    await sock.sendMessage(targetChat, {
                        image: fs.readFileSync(imagePath),
                        caption: instruction.caption || ""
                    });
                    console.log(`✅ Imagen enviada a ${targetChat}`);
                } else {
                    console.error(`❌ Imagen no encontrada: ${imagePath}`);
                }
            }

            // 3. Borrar mensaje del grupo
            if (instruction.delete_message && instruction.message_key) {
                console.log("🗑️ Intentando borrar mensaje del grupo...");
                const messageKey = JSON.parse(instruction.message_key);
                console.log("   Key parseada:", messageKey);
                await deleteMessageFromGroup(sock, messageKey);
            }

            // 4. Expulsar usuario del grupo
            if (instruction.remove_user && instruction.chat_id && instruction.participant_jid) {
    await removeUserFromGroup(
        sock,
        instruction.chat_id,
        instruction.participant_jid
    );
}


        } catch (error) {
            console.error("❌ Error procesando instrucción:", error);
        }
    }
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState("auth");

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: true,
    connectTimeoutMs: 60000,
    keepAliveIntervalMs: 25000,
    emitOwnEvents: true,
    defaultQueryTimeoutMs: 0,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      qrcode.generate(qr, { small: true });
    }

    if (connection === "close") {
    socketReady = false;
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      console.log(`Conexión cerrada. Razón: ${reason}`);

      if (reason !== DisconnectReason.loggedOut) {
        console.log("Reconectando en 5 segundos...");
        setTimeout(start, 5000);
      }
    }

    if (connection === "open") {
  console.log("✅ WhatsApp conectado y listo para enviar mensajes");
  socketReady = true;
}

  });

  // ============================================
  // MANEJADOR PRINCIPAL DE MENSAJES
  // ============================================

  sock.ev.on("messages.upsert", async ({ messages }) => {
    try {
      const msg = messages[0];
      if (!msg.message) return;

      // Ignorar mensajes propios
      if (msg.key.fromMe) {
        return;
      }

      const chatId = msg.key.remoteJid;
      const isGroup = chatId.endsWith("@g.us");
      // PARTICIPANT REAL (clave para moderación)
      const participantJid = isGroup
         ? msg.key.participant
         : msg.key.remoteJid;
      console.log("👤 participantJid:", participantJid);


      const messageType = Object.keys(msg.message)[0];

      console.log(`\n📨 Nuevo mensaje recibido:`);
      console.log(`   Chat: ${isGroup ? 'Grupo' : 'Privado'}`);
      console.log(`   ID: ${chatId}`);

      // Obtener número del remitente
      let sender = "";
      if (isGroup) {
        sender = msg.key.participant?.split("@")[0] || "";
      } else {
        sender = chatId.split("@")[0];
      }

      const pushName = msg.pushName || "Usuario";

      // ============================================
      // 1. MENSAJES EN GRUPO (DETECCIÓN DE VENTAS)
      // ============================================
      if (isGroup && chatId === GROUP_ID) {
        console.log(`   👥 Grupo monitoreado`);

        let messageContent = "";
        let mediaFilename = null;

        // Extraer contenido según tipo
        if (messageType === "conversation") {
          messageContent = msg.message.conversation || "";
        } else if (messageType === "extendedTextMessage") {
          messageContent = msg.message.extendedTextMessage?.text || "";
        }

        console.log(`   📝 Contenido: ${messageContent.substring(0, 100)}`);

        // Detectar palabras clave de venta
        const lowerContent = messageContent.toLowerCase();
        const hasSalesKeyword = SALES_KEYWORDS.some(keyword =>
          lowerContent.includes(keyword)
        );

        console.log(`   🔍 ¿Palabra clave de venta?: ${hasSalesKeyword}`);

        // Si es imagen o tiene palabra clave, procesar
        if (messageType === "imageMessage" || hasSalesKeyword) {
          console.log(`🚨 Mensaje sospechoso en grupo de: ${sender}`);

          // Si es imagen, descargarla
          if (messageType === "imageMessage") {
            try {
              const buffer = await downloadMediaMessage(
                msg,
                "buffer",
                {},
                { logger: undefined, retryCount: 3 }
              );

              mediaFilename = `img_${Date.now()}_${sender}.jpg`;
              const filepath = path.join(IMAGE_DIR, mediaFilename);
              fs.writeFileSync(filepath, buffer);

              console.log(`📸 Imagen guardada: ${mediaFilename}`);
            } catch (error) {
              console.error("❌ Error descargando imagen:", error.message);
            }
          }

          // Enviar a la API para crear caso
          try {
            const payload = {
              phone: sender,
              name: pushName,
              chat_id: chatId,
              is_group: true,
              message_type: messageType === "imageMessage" ? "image" : "text",
              content: messageType === "imageMessage" ? mediaFilename : messageContent,
              whatsapp_message_key: JSON.stringify(msg.key),
               participant_jid: participantJid
            };

            console.log("📤 Enviando a /ingest_message...");
            const response = await axios.post(`${API_BASE_URL}/ingest_message`, payload, {
              timeout: 10000
            });
            console.log("✅ API respondió:", response.data);

          } catch (error) {
            console.error("❌ Error enviando a API:", error.message);
            if (error.response) {
              console.error("   Detalles:", error.response.data);
            }
          }
        } else {
          console.log(`   ✅ Mensaje normal, ignorando.`);
        }
        return;
      }

      // ============================================
      // 2. MENSAJES PRIVADOS
      // ============================================
      if (!isGroup) {
        let messageText = "";

        if (messageType === "conversation") {
          messageText = msg.message.conversation || "";
        } else if (messageType === "extendedTextMessage") {
          messageText = msg.message.extendedTextMessage?.text || "";
        }

        console.log(`💬 Mensaje privado de ${sender}: ${messageText}`);

        // Si es una respuesta numérica (1, 2, 3), enviar a /moderation/response
        if (/^[123]$/.test(messageText.trim())) {
          console.log(`🔢 Respuesta numérica detectada: ${messageText}`);
          try {
            const payload = {
              phone: sender,
              response: messageText.trim()
            };

            console.log("📤 Enviando a /moderation/response:", payload);
            const response = await axios.post(`${API_BASE_URL}/moderation/response`, payload);
            console.log("✅ Respuesta de /moderation/response:", response.data);

            if (response.data.instructions) {
              await processInstructions(response.data.instructions, sock, chatId);
            }
          } catch (error) {
            console.error("Error en /moderation/response:", error.message);
          }
          return;
        }

        // Si no es numérico, enviar a /conversation
        try {
          const payload = {
            phone: sender,
            message: messageText,
            name: pushName,
            reply_jid: chatId
          };

          console.log("📤 Enviando a /conversation:", payload);
          const response = await axios.post(`${API_BASE_URL}/conversation`, payload);
          console.log("✅ Respuesta de /conversation:", response.data);

          if (response.data.instructions) {
            await processInstructions(response.data.instructions, sock, chatId);
          }
        } catch (error) {
          console.error("Error en /conversation:", error.message);
        }
        return;
      }

      // ============================================
      // 3. OTROS GRUPOS (IGNORAR)
      // ============================================
      if (isGroup && chatId !== GROUP_ID) {
        console.log(`   👥 Grupo no monitoreado, ignorando`);
        return;
      }

    } catch (error) {
      console.error("❌ Error general en el manejador de mensajes:", error);
    }
  });
}

// ============================================
// MANEJAR ERRORES GLOBALES
// ============================================
process.on('uncaughtException', (error) => {
  console.error('❌ Error no capturado:', error);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('❌ Promesa rechazada no manejada:', reason);
});

process.on('SIGINT', () => {
  console.log('\n🛑 Recibida señal de interrupción, cerrando...');
  process.exit(0);
});

// Iniciar el bot
console.log("🚀 Iniciando bot moderador de WhatsApp...");
console.log(`🌐 API: ${API_BASE_URL}`);
console.log(`👥 Grupo monitoreado: ${GROUP_ID}`);
console.log("📸 Directorio de imágenes:", IMAGE_DIR);
console.log("==========================================");

start();