#!/bin/bash
set -e

cd /home/raspbery/services/bot_moderador/whatsapp

export NODE_ENV=production
export HOME=/home/raspbery

exec /usr/bin/node index.js

