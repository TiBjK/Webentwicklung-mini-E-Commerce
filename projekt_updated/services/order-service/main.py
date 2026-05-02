"""
Order Service – Nimmt Bestellungen entgegen und koordiniert mit Inventory & Notification.
Daten werden in data/orders.json gespeichert.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import json, os, time, uuid
import httpx

app = FastAPI(title="Order Service", description="Bestellverwaltung", version="1.0.0")

DATA_FILE    = os.getenv("DATA_FILE",           "data/orders.json")
INVENTORY_URL   = os.getenv("INVENTORY_SERVICE_URL",    "http://inventory-service:8002")
NOTIFICATION_URL = os.getenv("NOTIFICATION_SERVICE_URL","http://notification-service:8004")

# ── Pydantic-Modelle ───────────────────────────────────────────────────────────
class OrderItem(BaseModel):
    product_id: str = Field(..., example="abc-123")
    quantity: int = Field(..., gt=0, example=2)
    unit_price: float = Field(..., gt=0, example=49.99)

class OrderCreate(BaseModel):
    customer_name: str = Field(..., example="Max Mustermann")
    customer_email: str = Field(..., example="max@example.com")
    items: list[OrderItem]

class Order(OrderCreate):
    id: str
    status: str          # pending | confirmed | cancelled
    total: float
    created_at: float

# ── Datei-Persistenz ───────────────────────────────────────────────────────────
def _load() -> dict:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        _save({})
    with open(DATA_FILE) as f:
        return json.load(f)

def _save(data: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Hilfsfunktionen ────────────────────────────────────────────────────────────
async def _reserve_inventory(product_id: str, quantity: int) -> bool:
    """Versucht Bestand zu reservieren. Gibt False zurück bei Fehler."""
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            r = await client.post(f"{INVENTORY_URL}/inventory/reserve",
                                  json={"product_id": product_id, "quantity": quantity})
            return r.status_code == 200
        except Exception:
            return False

async def _send_notification(order, status: str):
    """Schickt eine Benachrichtigung mit vollem Payload an den Notification-Service."""
    items = order.items if hasattr(order, "items") else order.get("items", [])
    if hasattr(items[0] if items else None, "product_id"):
        summary = ", ".join(f"{i.quantity}x {i.product_id}" for i in items)
        email = order.customer_email
        name = order.customer_name
        total = order.total
        oid = order.id
    else:
        summary = ", ".join(f"{i['quantity']}x {i['product_id']}" for i in items)
        email = order.get("customer_email", "")
        name = order.get("customer_name", "")
        total = order.get("total", 0)
        oid = order if isinstance(order, str) else order.get("id", "")
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            await client.post(f"{NOTIFICATION_URL}/notifications",
                              json={"order_id": oid, "email": email, "status": status,
                                    "customer_name": name, "total": total,
                                    "product_summary": summary, "timestamp": time.time()})
        except Exception:
            pass  # Notification ist optional – Bestellung trotzdem speichern

# ── Endpunkte ──────────────────────────────────────────────────────────────────
@app.get("/orders", response_model=list[Order], tags=["Orders"])
async def list_orders():
    """Alle Bestellungen auflisten."""
    return list(_load().values())

@app.post("/orders", response_model=Order, status_code=201, tags=["Orders"])
async def create_order(body: OrderCreate):
    """
    Neue Bestellung aufgeben.
    - Reserviert automatisch Bestand beim Inventory-Service.
    - Schickt Benachrichtigung an den Notification-Service.
    """
    # Bestand reservieren
    for item in body.items:
        ok = await _reserve_inventory(item.product_id, item.quantity)
        if not ok:
            raise HTTPException(409, f"Nicht genug Bestand für Produkt {item.product_id}")

    total = sum(i.quantity * i.unit_price for i in body.items)
    order = Order(
        id=str(uuid.uuid4()),
        status="confirmed",
        total=round(total, 2),
        created_at=time.time(),
        **body.model_dump(),
    )
    db = _load()
    db[order.id] = order.model_dump()
    _save(db)

    # Benachrichtigung asynchron senden
    await _send_notification(order, "confirmed")
    return order

@app.get("/orders/{order_id}", response_model=Order, tags=["Orders"])
async def get_order(order_id: str):
    """Eine Bestellung per ID abrufen."""
    db = _load()
    if order_id not in db:
        raise HTTPException(404, "Bestellung nicht gefunden")
    return db[order_id]

@app.put("/orders/{order_id}/cancel", response_model=Order, tags=["Orders"])
async def cancel_order(order_id: str):
    """Bestellung stornieren."""
    db = _load()
    if order_id not in db:
        raise HTTPException(404, "Bestellung nicht gefunden")
    db[order_id]["status"] = "cancelled"
    _save(db)
    await _send_notification(db[order_id], "cancelled")
    return db[order_id]

@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    db = _load()
    statuses = {}
    for o in db.values():
        statuses[o["status"]] = statuses.get(o["status"], 0) + 1
    return {"service": "order-service", "status": "ok",
            "total_orders": len(db), "by_status": statuses, "uptime": int(time.time())}

@app.get("/health", tags=["Monitoring"])
async def health():
    return {"status": "ok"}
