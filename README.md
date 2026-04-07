# Transferr

Microservicio de transferencia de archivos con dashboard web. Recibe archivos vía API REST y los mueve a destinos predefinidos según un archivo de configuración. Opcionalmente notifica via Gotify al completar cada transferencia.

---

## Características

- API REST para recibir y mover archivos
- Destinos configurables via `config.yml`
- Dashboard web con estética de terminal
- Notificaciones opcionales via Gotify
- Healthcheck integrado

---

## Requisitos

- Docker y Docker Compose
- (Opcional) Instancia de [Gotify](https://gotify.net/) para notificaciones

---

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/osdaeg/transferr.git
cd transferr
```

### 2. Crear el archivo `.env`

```bash
cp .env.example .env
nano .env
```

Completar con tus valores:

```bash
GOTIFY_URL=http://TU_HOST:8088
GOTIFY_TOKEN=tu_token_aqui
PUID=1000
PGID=1000
TZ=America/Argentina/Buenos_Aires
```

Si no usás Gotify, podés dejar `GOTIFY_TOKEN` vacío — las notificaciones se omiten silenciosamente.

### 3. Configurar destinos

Editá `config.yml` con los destinos que necesitás. El archivo incluido tiene ejemplos comentados.

### 4. Montar los destinos en el compose

En `docker-compose.yml`, descomentá y ajustá los volúmenes de destino para que coincidan con los definidos en `config.yml`:

```yaml
volumes:
  - /ruta/local/comics:/comics
  - /ruta/local/music:/music
```

### 5. Crear la red Docker (si no existe)

```bash
docker network create TuRed
```

### 6. Levantar el servicio

```bash
docker compose up -d --build
```

---

## Uso

El dashboard está disponible en `http://TU_HOST:7900`.

Para transferir un archivo via API:

```bash
curl -X POST http://TU_HOST:7900/transfer \
  -F "file=@/ruta/al/archivo.ext" \
  -F "destination=comics"
```

Ver `ejemplos_curl.sh` para más ejemplos de uso.

---

## Estructura

```
transferr/
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── config.yml
├── main.py
├── requirements.txt
├── ejemplos_curl.sh
├── static/
│   └── index.html
└── logs/               # generado en runtime, no incluido en el repo
```

---

## Licencia

AGPL
