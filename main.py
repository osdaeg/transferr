import os
import shutil
import logging
import httpx
import yaml
from collections import deque
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import tempfile

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = "/logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "transferr.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("transferr")

# ── Config ────────────────────────────────────────────────────────────────────
GOTIFY_URL   = os.getenv("GOTIFY_URL", "http://192.168.1.10:8088")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "")
CONFIG_PATH  = os.getenv("CONFIG_PATH", "/config/config.yml")

# Historial en memoria (últimas 200 transferencias)
transfer_history: deque = deque(maxlen=200)
stats = {
    "total_transfers": 0,
    "total_bytes":     0,
    "total_errors":    0,
    "started_at":      datetime.now().isoformat(),
}


def load_destinations() -> dict[str, str]:
    try:
        with open(CONFIG_PATH, "r") as f:
            data = yaml.safe_load(f)
        destinations = data.get("destinations", {})
        if not destinations:
            log.warning("config.yml no tiene entradas en 'destinations'.")
        return {k: str(v) for k, v in destinations.items()}
    except FileNotFoundError:
        log.error(f"Archivo de configuración no encontrado: {CONFIG_PATH}")
        return {}
    except yaml.YAMLError as exc:
        log.error(f"Error al parsear config.yml: {exc}")
        return {}


# ── Gotify ────────────────────────────────────────────────────────────────────
async def notify(title: str, message: str, priority: int = 5) -> None:
    if not GOTIFY_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{GOTIFY_URL}/message",
                params={"token": GOTIFY_TOKEN},
                json={"title": title, "message": message, "priority": priority},
            )
    except Exception as exc:
        log.warning(f"No se pudo enviar notificación a Gotify: {exc}")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="transferr", version="1.2.0")

STATIC_DIR = "/app/static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    dashboard_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r") as f:
            return f.read()
    return HTMLResponse("<h1>Dashboard no encontrado</h1>", status_code=404)


@app.get("/health")
async def health():
    destinations = load_destinations()
    return {
        "status": "ok",
        "version": "1.2.0",
        "uptime_since": stats["started_at"],
        "destinations": list(destinations.keys()),
    }


@app.get("/api/stats")
async def get_stats():
    destinations = load_destinations()

    dest_status = {}
    for alias, path in destinations.items():
        try:
            accessible = os.path.isdir(path) and os.access(path, os.W_OK)
        except Exception:
            accessible = False
        dest_status[alias] = {"path": path, "accessible": accessible}

    bytes_by_dest: dict[str, int] = {}
    count_by_dest: dict[str, int] = {}
    for entry in transfer_history:
        d = entry["destination"]
        bytes_by_dest[d] = bytes_by_dest.get(d, 0) + entry.get("size_bytes", 0)
        count_by_dest[d] = count_by_dest.get(d, 0) + 1

    return {
        "total_transfers": stats["total_transfers"],
        "total_bytes":     stats["total_bytes"],
        "total_errors":    stats["total_errors"],
        "started_at":      stats["started_at"],
        "destinations":    dest_status,
        "bytes_by_dest":   bytes_by_dest,
        "count_by_dest":   count_by_dest,
        "recent":          list(reversed(list(transfer_history)))[:50],
    }


@app.get("/destinations")
async def list_destinations():
    return {"destinations": load_destinations()}


@app.post("/transfer")
async def transfer(
    file: UploadFile = File(...),
    destination: str = Form(...),
    subfolder: str  = Form(""),
):
    started  = datetime.now().isoformat()
    filename = file.filename or "unknown"

    destinations = load_destinations()
    log.info(f"Recibido '{filename}' → destino='{destination}' subfolder='{subfolder}'")

    if destination not in destinations:
        msg = f"Destino desconocido: '{destination}'. Válidos: {list(destinations.keys())}"
        log.error(msg)
        stats["total_errors"] += 1
        transfer_history.append({
            "filename": filename, "destination": destination,
            "success": False, "error": msg,
            "started": started, "finished": datetime.now().isoformat(),
            "size_bytes": 0,
        })
        raise HTTPException(status_code=400, detail=msg)

    base_path  = destinations[destination]
    target_dir = os.path.join(base_path, subfolder) if subfolder else base_path

    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as exc:
        msg = f"No se pudo crear directorio '{target_dir}': {exc}"
        log.error(msg)
        stats["total_errors"] += 1
        raise HTTPException(status_code=500, detail=msg)

    target_path = os.path.join(target_dir, filename)

    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        shutil.move(tmp_path, target_path)
    except Exception as exc:
        msg = f"Error al copiar '{filename}' a '{target_path}': {exc}"
        log.error(msg)
        stats["total_errors"] += 1
        transfer_history.append({
            "filename": filename, "destination": destination,
            "success": False, "error": msg,
            "started": started, "finished": datetime.now().isoformat(),
            "size_bytes": 0,
        })
        await notify("transferr ❌ Error", msg, priority=8)
        raise HTTPException(status_code=500, detail=msg)
    finally:
        file.file.close()

    size_bytes = os.path.getsize(target_path)
    size_kb    = round(size_bytes / 1024, 2)
    finished   = datetime.now().isoformat()

    stats["total_transfers"] += 1
    stats["total_bytes"]     += size_bytes

    transfer_history.append({
        "filename":    filename,
        "destination": destination,
        "path":        target_path,
        "success":     True,
        "size_bytes":  size_bytes,
        "size_kb":     size_kb,
        "started":     started,
        "finished":    finished,
    })

    log.info(f"✓ '{filename}' copiado a '{target_path}' ({size_kb} KB)")

    await notify(
        title=f"transferr ✅ {destination}",
        message=f"Archivo: {filename}\nDestino: {target_path}\nTamaño: {size_kb} KB",
    )

    return JSONResponse({
        "success":     True,
        "filename":    filename,
        "destination": destination,
        "path":        target_path,
        "size_bytes":  size_bytes,
        "size_kb":     size_kb,
        "started":     started,
        "finished":    finished,
    })
