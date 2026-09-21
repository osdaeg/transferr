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


def parse_mode(mode_val) -> int | None:
    """Convierte '644', '0644', '0o644' o un entero YAML a int octal."""
    if mode_val is None:
        return None
    if isinstance(mode_val, int):
        # YAML puede parsear 0644 como entero decimal 420 — lo tomamos tal cual
        return mode_val
    s = str(mode_val).strip()
    if s.startswith("0o") or s.startswith("0O"):
        return int(s, 8)
    if s.startswith("0") and len(s) > 1:
        return int(s, 8)
    # string tipo "644" → octal
    return int(s, 8)


def load_destinations() -> dict[str, dict]:
    """
    Retorna un dict con la configuración completa de cada destino:
      {
        "alias": {
          "path": "/ruta",
          "uid":  1000 | None,
          "gid":  1000 | None,
          "mode": 0o644 | None,
        }, ...
      }
    Soporta formato simple (alias: /ruta) y extendido (alias: {path, uid, gid, mode}).
    """
    try:
        with open(CONFIG_PATH, "r") as f:
            data = yaml.safe_load(f)
        raw = data.get("destinations", {})
        if not raw:
            log.warning("config.yml no tiene entradas en 'destinations'.")
            return {}

        result = {}
        for alias, value in raw.items():
            if isinstance(value, str):
                # Formato simple
                result[alias] = {"path": value, "uid": None, "gid": None, "mode": None}
            elif isinstance(value, dict):
                # Formato extendido
                result[alias] = {
                    "path": str(value.get("path", "")),
                    "uid":  int(value["uid"])  if value.get("uid")  is not None else None,
                    "gid":  int(value["gid"])  if value.get("gid")  is not None else None,
                    "mode": parse_mode(value.get("mode")),
                }
            else:
                log.warning(f"Destino '{alias}' con formato inválido, ignorado.")
        return result

    except FileNotFoundError:
        log.error(f"Archivo de configuración no encontrado: {CONFIG_PATH}")
        return {}
    except yaml.YAMLError as exc:
        log.error(f"Error al parsear config.yml: {exc}")
        return {}


def apply_permissions(path: str, uid: int | None, gid: int | None, mode: int | None) -> None:
    """Aplica chown y/o chmod al archivo si están configurados."""
    if uid is not None or gid is not None:
        effective_uid = uid if uid is not None else -1   # -1 = sin cambio
        effective_gid = gid if gid is not None else -1
        try:
            os.chown(path, effective_uid, effective_gid)
            log.info(f"chown {effective_uid}:{effective_gid} → '{path}'")
        except PermissionError:
            log.warning(
                f"No se pudo hacer chown en '{path}'. "
                "El contenedor debe correr como root para cambiar el dueño. "
                "Quitá 'user:' del docker-compose.yml."
            )
        except Exception as exc:
            log.warning(f"chown falló en '{path}': {exc}")

    if mode is not None:
        try:
            os.chmod(path, mode)
            log.info(f"chmod {oct(mode)} → '{path}'")
        except Exception as exc:
            log.warning(f"chmod falló en '{path}': {exc}")


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
app = FastAPI(title="transferr", version="1.3.0")

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
        "version": "1.3.0",
        "uptime_since": stats["started_at"],
        "destinations": list(destinations.keys()),
    }


@app.get("/api/stats")
async def get_stats():
    destinations = load_destinations()

    dest_status = {}
    for alias, cfg in destinations.items():
        path = cfg["path"]
        try:
            accessible = os.path.isdir(path) and os.access(path, os.W_OK)
        except Exception:
            accessible = False
        dest_status[alias] = {
            "path":       path,
            "accessible": accessible,
            "uid":        cfg["uid"],
            "gid":        cfg["gid"],
            "mode":       oct(cfg["mode"]) if cfg["mode"] is not None else None,
        }

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
    dests = load_destinations()
    # Serializar mode a string legible
    result = {}
    for alias, cfg in dests.items():
        result[alias] = {**cfg, "mode": oct(cfg["mode"]) if cfg["mode"] is not None else None}
    return {"destinations": result}


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

    dest_cfg   = destinations[destination]
    base_path  = dest_cfg["path"]
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
        apply_permissions(target_path, dest_cfg["uid"], dest_cfg["gid"], dest_cfg["mode"])
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
