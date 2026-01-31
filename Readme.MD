# 🤖 Bot Moderador de WhatsApp

Sistema automatizado de moderación para grupos de WhatsApp con gestión de infracciones, strikes y apelaciones.

## ✨ Características

- ✅ **Detección automática** de ventas y contenido inapropiado
- ⚠️ **Sistema de strikes** (3 strikes = expulsión)
- 📸 **Revisión manual** de imágenes sospechosas
- 📝 **Sistema de apelaciones** para usuarios
- 🛡️ **Panel de moderación** con comandos interactivos
- 📊 **Historial completo** de acciones y casos
- 🔄 **Reconexión automática** de WhatsApp

## 🚀 Inicio Rápido

### Requisitos
- Python 3.10+
- Node.js 18+
- 1 GB RAM mínimo

### Instalación Local (Windows)

1. **Clonar el repositorio**
```bash
git clone https://github.com/TU-USUARIO/bot-moderador.git
cd bot-moderador
```

2. **Configurar variables de entorno**
```bash
copy .env.example .env
notepad .env
```

Edita el archivo `.env`:
```env
GROUP_ID=TU_GROUP_ID@g.us
ADMIN_PHONE=TU_TELEFONO
```

3. **Ejecutar el bot**
```bash
start.bat
```

4. **Escanear QR** con WhatsApp (Ajustes > Dispositivos vinculados)

### Instalación Local (Linux/Mac)

1. **Clonar y configurar**
```bash
git clone https://github.com/TU-USUARIO/bot-moderador.git
cd bot-moderador
cp .env.example .env
nano .env
```

2. **Ejecutar**
```bash
chmod +x start.sh
./start.sh
```

## 📱 ¿Cómo Cambiar de Grupo?

Solo necesitas cambiar el `GROUP_ID` en el archivo `.env`:

1. **Obtener el ID del grupo**:
   - En WhatsApp Web, abre el grupo
   - La URL será: `https://web.whatsapp.com/send?jid=XXXXXXXXX@g.us`
   - Copia el número completo con `@g.us`

2. **Actualizar .env**:
   ```env
   GROUP_ID=NUEVO_ID@g.us
   ```

3. **Reiniciar el bot**

## 🛠️ Comandos

### Para Usuarios
- `strikes` - Ver tus advertencias
- `reglas` - Ver reglas del grupo
- `apelar` - Apelar una sanción

### Para Moderadores
- `estoy` - Ver siguiente caso pendiente
- Responder con `1`, `2` o `3` según las opciones mostradas

### Para Administradores
- `agregar mod +54XXXXXXXXX` - Agregar moderador
- `quitar mod +54XXXXXXXXX` - Quitar moderador

## 📊 Mantenimiento

### Limpieza Automática
```bash
python maintenance.py
```

Esto:
- Limpia imágenes antiguas (>30 días)
- Archiva casos resueltos
- Crea backup de la base de datos
- Muestra estadísticas

### Ver Base de Datos
```bash
python check_db.py
```

## 🌐 Deployment en DonWeb

Ver guía completa: [DEPLOYMENT_DONWEB.md](DEPLOYMENT_DONWEB.md)

**Resumen**:
1. Contratar servidor Ubuntu (1 vCore, 1 GB RAM)
2. Instalar Python, Node.js, PM2
3. Subir proyecto y configurar `.env`
4. `pm2 start ecosystem.config.cjs`
5. Escanear QR de WhatsApp

## 🔧 Configuración Avanzada

### Cambiar Cantidad de Strikes
En `.env`:
```env
MAX_STRIKES=3  # Cambiar a 2, 4, etc.
```

### Agregar Palabras Clave
En `whatsapp/index.js`, línea 18:
```javascript
const SALES_KEYWORDS = ["vendo", "compro", "TU_PALABRA"];
```

### Cambiar Retención de Imágenes
En `.env`:
```env
IMAGE_RETENTION_DAYS=30  # Días antes de borrar
```

## 📁 Estructura del Proyecto

```
bot-moderador/
├── app/
│   ├── handlers/         # Lógica de conversación
│   ├── models/          # Modelos de base de datos
│   ├── utils/           # Utilidades
│   └── main.py          # API FastAPI
├── whatsapp/
│   ├── index.js         # Bot de WhatsApp
│   └── package.json
├── media/               # Imágenes temporales
├── logs/                # Archivos de log
├── .env                 # Configuración (NO subir a Git)
├── start.bat            # Inicio rápido Windows
├── start.sh             # Inicio rápido Linux/Mac
└── maintenance.py       # Script de mantenimiento
```

## 🐛 Solución de Problemas

### El bot no detecta mensajes
- Verifica que `GROUP_ID` sea correcto
- Revisa que WhatsApp esté conectado: `pm2 logs bot-whatsapp`

### WhatsApp se desconecta constantemente
```bash
rm -rf whatsapp/auth
pm2 restart bot-whatsapp
# Volver a escanear QR
```

### La API no responde
```bash
pm2 logs bot-api
# Verificar puerto 8000 libre
```

