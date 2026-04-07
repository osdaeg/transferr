# ══════════════════════════════════════════════════════════════════════════════
# transferr — Ejemplos de llamadas curl
# ══════════════════════════════════════════════════════════════════════════════
# El servidor escucha en: http://192.168.1.10:7900
# Desde OTRO contenedor (red interna Docker): http://transferr:8000
# ══════════════════════════════════════════════════════════════════════════════


# ── 1. Healthcheck ────────────────────────────────────────────────────────────
curl http://192.168.1.10:7900/health


# ── 2. Ver destinos disponibles ───────────────────────────────────────────────
curl http://192.168.1.10:7900/destinations


# ── 3. Copiar un MP3 descargado por aMule → slskd ────────────────────────────
curl -s -X POST http://192.168.1.10:7900/transfer \
  -F "file=@/ruta/del/archivo/cancion.mp3" \
  -F "destination=slskd"

# Respuesta esperada:
# {
#   "success": true,
#   "filename": "cancion.mp3",
#   "destination": "slskd",
#   "path": "/slskd/cancion.mp3",
#   "size_bytes": 8945321,
#   "size_kb": 8735.47,
#   "started": "2025-06-01T14:22:01.123456",
#   "finished": "2025-06-01T14:22:01.456789"
# }


# ── 4. Copiar un EPUB descargado por qBittorrent → calibre ───────────────────
curl -s -X POST http://192.168.1.10:7900/transfer \
  -F "file=@/ruta/del/archivo/libro.epub" \
  -F "destination=calibre"


# ── 5. Copiar un CBZ → comics, con subcarpeta ─────────────────────────────────
curl -s -X POST http://192.168.1.10:7900/transfer \
  -F "file=@/ruta/del/archivo/batman_001.cbz" \
  -F "destination=comics" \
  -F "subfolder=Batman"


# ── 6. Uso desde un script bash (ej: script post-descarga de aMule) ───────────
#!/bin/bash
# amule_on_download.sh
# aMule llama a este script con la ruta del archivo como $1

FILE="$1"
FILENAME=$(basename "$FILE")

RESPONSE=$(curl -s -X POST http://192.168.1.10:7900/transfer \
  -F "file=@${FILE}" \
  -F "destination=slskd")

SUCCESS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['success'])")

if [ "$SUCCESS" = "True" ]; then
  echo "✓ $FILENAME transferido correctamente"
else
  echo "✗ Error transfiriendo $FILENAME"
  echo "$RESPONSE"
fi


# ── 7. Desde otro contenedor (misma red Docker) ───────────────────────────────
# Si ambos contenedores están en la misma red Docker, usá el nombre del servicio:
curl -s -X POST http://transferr:8000/transfer \
  -F "file=@/downloads/comic.cbz" \
  -F "destination=comics" \
  -F "subfolder=Marvel"
