"""
Notification Service – Empfängt Bestellbenachrichtigungen und streamt sie per SSE.

ÄNDERUNGEN gegenüber der alten Version:
- SSE verwendet Polling auf der JSON-Datei statt eines In-Memory Queues.
  → Funktioniert korrekt mit mehreren Kubernetes-Replicas (kein shared State nötig).
- Erweitertes Datenmodell: Produktname, Gesamtbetrag und Typ sind jetzt enthalten.
- CORS-Header sind korrekt gesetzt.
- DATA_FILE Pfad-Bug bei fehlendem Verzeichnis ist behoben.
- Neuer DELETE-Endpoint zum Löschen aller Benachrichtigungen (für Admin-UI).
- /notifications/unread gibt nur neue Einträge seit einem Zeitstempel zurück.
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json, os, time, asyncio, uuid

app = FastAPI(
    title="Notification Service",
    description="Bestellbenachrichtigungen & Server-Sent Events (SSE)",
    version="2.0.0",
)

# CORS – erlaubt Zugriffe vom Browser (Gateway auf Port 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILE = os.getenv("DATA_FILE", "data/notifications.json")

# SSE Polling-Intervall in Sekunden
SSE_POLL_INTERVAL = float(os.getenv("SSE_POLL_INTERVAL", "1.5"))

# ── Pydantic-Modelle ───────────────────────────────────────────────────────────

class NotificationIn(BaseModel):
    order_id: str
    email: str
    status: str          # confirmed | cancelled | shipped | refunded
    # Optionale Zusatzfelder für reichhaltigere Benachrichtigungen
    customer_name: str | None = None
    total: float | None = None
    product_summary: str | None = None   # z.B. "2x Laptop, 1x Maus"
    timestamp: float | None = None

class Notification(NotificationIn):
    id: str
    received_at: float

# ── Datei-Persistenz ───────────────────────────────────────────────────────────

def _ensure_dir():
    """Stellt sicher dass das Daten-Verzeichnis existiert (auch wenn DATA_FILE flach ist)."""
    dir_part = os.path.dirname(DATA_FILE)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)

def _load() -> list:
    _ensure_dir()
    if not os.path.exists(DATA_FILE):
        _save([])
    with open(DATA_FILE) as f:
        return json.load(f)

def _save(data: list):
    _ensure_dir()
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Endpunkte ──────────────────────────────────────────────────────────────────

@app.post("/notifications", response_model=Notification, status_code=201, tags=["Notifications"])
async def receive_notification(body: NotificationIn):
    """
    Neue Benachrichtigung speichern.
    Wird vom Order-Service aufgerufen (bei Bestellung oder Stornierung).
    SSE-Clients erhalten die neue Benachrichtigung automatisch beim naechsten Poll.
    """
    notif = Notification(
        id=str(uuid.uuid4()),
        received_at=time.time(),
        timestamp=body.timestamp or time.time(),
        **{k: v for k, v in body.model_dump().items() if k != "timestamp"},
    )
    db = _load()
    db.append(notif.model_dump())
    _save(db)
    return notif


@app.get("/notifications", response_model=list[Notification], tags=["Notifications"])
async def list_notifications():
    """Alle gespeicherten Benachrichtigungen abrufen (neueste zuerst)."""
    return sorted(_load(), key=lambda n: n.get("received_at", 0), reverse=True)


@app.get("/notifications/unread", response_model=list[Notification], tags=["Notifications"])
async def unread_notifications(since: float = Query(0.0, description="Unix-Timestamp; gibt nur Eintraege zurueck, die neuer sind")):
    """
    Nur Benachrichtigungen seit einem bestimmten Zeitstempel zurueckgeben.
    Nuetzlich fuer Polling-basierte Clients ohne SSE-Unterstuetzung.
    """
    return [n for n in _load() if n.get("received_at", 0) > since]


@app.delete("/notifications", status_code=204, tags=["Notifications"])
async def clear_notifications():
    """Alle Benachrichtigungen loeschen (nur fuer Admin-Zwecke)."""
    _save([])


@app.get("/notifications/stream", tags=["Notifications"])
async def sse_stream():
    """
    Server-Sent Events (SSE) Stream.

    Funktionsweise: Der Stream pollt die gespeicherten Benachrichtigungen alle
    ~1,5 Sekunden und schickt nur neue Eintraege (seit dem letzten Check) an den Client.
    Kein geteilter In-Memory-State – funktioniert mit mehreren Kubernetes-Replicas.

    Verbindung im Browser:
        const es = new EventSource('http://localhost:8004/notifications/stream');
        es.onmessage = e => console.log(JSON.parse(e.data));
    """
    async def event_generator():
        last_seen_at = time.time()

        yield f"data: {json.dumps({'message': 'Verbunden mit Notification-Stream', 'since': last_seen_at})}\n\n"

        heartbeat_counter = 0

        try:
            while True:
                await asyncio.sleep(SSE_POLL_INTERVAL)
                heartbeat_counter += 1

                try:
                    new_entries = [
                        n for n in _load()
                        if n.get("received_at", 0) > last_seen_at
                    ]
                except Exception:
                    new_entries = []

                if new_entries:
                    new_entries.sort(key=lambda n: n.get("received_at", 0))
                    for entry in new_entries:
                        yield f"data: {json.dumps(entry)}\n\n"
                    last_seen_at = new_entries[-1]["received_at"]

                if heartbeat_counter >= int(15 / SSE_POLL_INTERVAL):
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0

        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    db = _load()
    by_status: dict = {}
    for n in db:
        s = n.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "service": "notification-service",
        "status": "ok",
        "total_notifications": len(db),
        "by_status": by_status,
        "sse_poll_interval_seconds": SSE_POLL_INTERVAL,
        "uptime": int(time.time()),
    }


@app.get("/health", tags=["Monitoring"])
async def health():
    return {"status": "ok"}
