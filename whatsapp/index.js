import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState
} from "@whiskeysockets/baileys";

import axios from "axios";
import qrcode from "qrcode-terminal";
import { Boom } from "@hapi/boom";
import fs from "fs";
import path from "path";
import { downloadMediaMessage } from "@whiskeysockets/baileys";


const API_URL = "http://127.0.0.1:8000/ingest_message";

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState("auth");

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      qrcode.generate(qr, { small: true });
    }

    if (connection === "close") {
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
      if (reason !== DisconnectReason.loggedOut) {
        start();
      }
    }

    if (connection === "open") {
      console.log("🟢 WhatsApp conectado");
    }
  });

sock.ev.on("messages.upsert", async ({ messages }) => {
  const msg = messages[0];
  if (!msg.message) return;

  const chatId = msg.key.remoteJid;
  const isGroup = chatId.endsWith("@g.us");

  const phone =
    msg.key.participant?.split("@")[0] ||
    chatId.split("@")[0];

  let messageType = null;
  let content = null;

  // TEXTO
  if (
    msg.message.conversation ||
    msg.message.extendedTextMessage?.text
  ) {
    messageType = "text";
    content =
      msg.message.conversation ||
      msg.message.extendedTextMessage.text;
  }

  // IMAGEN
else if (msg.message.imageMessage) {
  messageType = "image";

  const buffer = await downloadMediaMessage(
    msg,
    "buffer",
    {},
    { logger: undefined }
  );

  const filename = `img_${Date.now()}_${phone}.jpg`;
  const filepath = path.join(IMAGE_DIR, filename);

  fs.writeFileSync(filepath, buffer);

  content = filename;
}


  // AUDIO / VIDEO → ignorar completamente
  else if (
    msg.message.audioMessage ||
    msg.message.videoMessage
  ) {
    return;
  }

  // Nada relevante
  if (!messageType) return;

  const payload = {
    phone,
    name: msg.pushName || null,
    chat_id: chatId,
    is_group: isGroup,
    message_type: messageType,
    content
  };

  try {
    const res = await axios.post(API_URL, payload);
    console.log("➡️ enviado a moderador:", res.data);
  } catch (err) {
    console.error("❌ error API:", err.message);
  }
});
}

const IMAGE_DIR = path.resolve("../media/temp/images");

if (!fs.existsSync(IMAGE_DIR)) {
  fs.mkdirSync(IMAGE_DIR, { recursive: true });
}


start();
