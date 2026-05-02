"""
Gateway Service – Einziger Zugangspunkt von außen.
Kümmert sich um Login/JWT, Rollen (RBAC) und leitet Anfragen weiter.

Rollen:
  admin  → Vollzugriff (Produkte anlegen/löschen, Bestände ändern, alle Bestellungen sehen)
  user   → Nur lesen & bestellen (Produkte lesen, eigene Bestellung aufgeben)
"""
from fastapi import FastAPI, Depends, HTTPException, Security, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import httpx
import jwt
import time
import os

app = FastAPI(
    title="Gateway Service",
    description="Zentraler Zugangspunkt – Login, RBAC & Routing",
    version="1.0.0",
)

# ── Konfiguration ──────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET", "super-geheimes-jwt-secret")
ALGORITHM  = "HS256"
TOKEN_TTL  = 3600  # 1 Stunde

SERVICE_URLS = {
    "products":      os.getenv("PRODUCT_SERVICE_URL",      "http://product-service:8001"),
    "inventory":     os.getenv("INVENTORY_SERVICE_URL",    "http://inventory-service:8002"),
    "orders":        os.getenv("ORDER_SERVICE_URL",        "http://order-service:8003"),
    "notifications": os.getenv("NOTIFICATION_SERVICE_URL","http://notification-service:8004"),
}

# ── Nutzerdatenbank mit Rollen ─────────────────────────────────────────────────
# Format: "username": {"password": "...", "role": "admin" | "user"}
USERS = {
    "admin": {"password": "password", "role": "admin"},
    "user":  {"password": "user123",  "role": "user"},
}

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("/app/frontend"):
    app.mount("/frontend", StaticFiles(directory="/app/frontend"), name="frontend")

security = HTTPBearer()

# ── Pydantic-Modelle ───────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_TTL
    role: str  # Rolle direkt mitschicken – praktisch fürs Frontend

class ProductBody(BaseModel):
    name: str = "Laptop Pro 15"
    description: str = "Testprodukt"
    price: float = 999.99
    category: str = "electronics"

class InventoryBody(BaseModel):
    quantity: int = 10

class OrderBody(BaseModel):
    customer_name: str = "Max Mustermann"
    customer_email: str = "max@example.com"
    items: list[dict] = [{"product_id": "PRODUCT_ID_HIER", "quantity": 2, "unit_price": 999.99}]

# ── JWT-Hilfsfunktionen ────────────────────────────────────────────────────────
def create_token(username: str, role: str) -> str:
    """Token erstellen – Rolle wird im Payload gespeichert."""
    payload = {
        "sub":  username,
        "role": role,           # ← Rolle steckt im Token drin
        "iat":  int(time.time()),
        "exp":  int(time.time()) + TOKEN_TTL,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    """Token prüfen und Payload zurückgeben (enthält sub + role)."""
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token abgelaufen – bitte neu einloggen")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Ungültiger Token")

def require_admin(token: dict = Depends(verify_token)) -> dict:
    """
    Dependency: Nur Admin darf durch.
    Gibt HTTP 403 zurück wenn die Rolle nicht 'admin' ist.
    """
    if token.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"Zugriff verweigert – Admin-Rechte erforderlich. Deine Rolle: '{token.get('role')}'"
        )
    return token

# ── Login ──────────────────────────────────────────────────────────────────────
@app.post("/login", response_model=TokenResponse, tags=["Auth"])
async def login(body: LoginRequest):
    """
    Einloggen und JWT-Token erhalten.

    - **admin / password** → Rolle: admin (Vollzugriff)
    - **user / user123**   → Rolle: user (nur lesen & bestellen)
    """
    user_data = USERS.get(body.username)
    if not user_data or user_data["password"] != body.password:
        raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")
    role  = user_data["role"]
    token = create_token(body.username, role)
    return TokenResponse(access_token=token, role=role)

@app.get("/api/me", tags=["Auth"])
async def me(token: dict = Depends(verify_token)):
    """Eigene Nutzerinfos – zeigt Rolle des eingeloggten Users."""
    return {
        "username": token.get("sub"),
        "role":     token.get("role"),
        "expires":  token.get("exp"),

        
    }

# ── Proxy-Hilfsfunktion ────────────────────────────────────────────────────────
async def _proxy(method: str, url: str, request: Request) -> Response:
    body    = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.request(method, url, content=body, headers=headers,
                                    params=dict(request.query_params))
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))

# ── Products ───────────────────────────────────────────────────────────────────
@app.get("/api/products", tags=["Products"])
async def get_products(request: Request, _: dict = Depends(verify_token)):
    """Alle Produkte abrufen.  admin + user"""
    return await _proxy("GET", f"{SERVICE_URLS['products']}/products", request)

@app.get("/api/products/{product_id}", tags=["Products"])
async def get_product(product_id: str, request: Request, _: dict = Depends(verify_token)):
    """Ein Produkt abrufen.  admin + user"""
    return await _proxy("GET", f"{SERVICE_URLS['products']}/products/{product_id}", request)

@app.post("/api/products", tags=["Products"])
async def create_product(body: ProductBody, request: Request, _: dict = Depends(require_admin)):
    """Neues Produkt anlegen.  Nur Admin"""
    return await _proxy("POST", f"{SERVICE_URLS['products']}/products", request)

@app.put("/api/products/{product_id}", tags=["Products"])
async def update_product(product_id: str, body: ProductBody, request: Request, _: dict = Depends(require_admin)):
    """Produkt aktualisieren.  Nur Admin"""
    return await _proxy("PUT", f"{SERVICE_URLS['products']}/products/{product_id}", request)

@app.delete("/api/products/{product_id}", tags=["Products"])
async def delete_product(product_id: str, request: Request, _: dict = Depends(require_admin)):
    """Produkt löschen.  Nur Admin"""
    return await _proxy("DELETE", f"{SERVICE_URLS['products']}/products/{product_id}", request)

# ── Inventory ──────────────────────────────────────────────────────────────────
@app.get("/api/inventory", tags=["Inventory"])
async def get_inventory(request: Request, _: dict = Depends(verify_token)):
    """Lagerbestand lesen.  admin + user"""
    return await _proxy("GET", f"{SERVICE_URLS['inventory']}/inventory", request)

@app.get("/api/inventory/{product_id}", tags=["Inventory"])
async def get_stock(product_id: str, request: Request, _: dict = Depends(verify_token)):
    """Bestand eines Produkts lesen.  admin + user"""
    return await _proxy("GET", f"{SERVICE_URLS['inventory']}/inventory/{product_id}", request)

@app.put("/api/inventory/{product_id}", tags=["Inventory"])
async def set_stock(product_id: str, body: InventoryBody, request: Request, _: dict = Depends(require_admin)):
    """Lagerbestand setzen.  Nur Admin"""
    return await _proxy("PUT", f"{SERVICE_URLS['inventory']}/inventory/{product_id}", request)

# ── Orders ─────────────────────────────────────────────────────────────────────
@app.post("/api/orders", tags=["Orders"])
async def create_order(body: OrderBody, request: Request, _: dict = Depends(verify_token)):
    """Neue Bestellung aufgeben.  admin + user"""
    return await _proxy("POST", f"{SERVICE_URLS['orders']}/orders", request)

@app.get("/api/orders", tags=["Orders"])
async def get_orders(request: Request, _: dict = Depends(require_admin)):
    """Alle Bestellungen abrufen.  Nur Admin"""
    return await _proxy("GET", f"{SERVICE_URLS['orders']}/orders", request)

@app.get("/api/orders/{order_id}", tags=["Orders"])
async def get_order(order_id: str, request: Request, _: dict = Depends(require_admin)):
    """Eine Bestellung abrufen.  Nur Admin"""
    return await _proxy("GET", f"{SERVICE_URLS['orders']}/orders/{order_id}", request)

@app.put("/api/orders/{order_id}/cancel", tags=["Orders"])
async def cancel_order(order_id: str, request: Request, _: dict = Depends(require_admin)):
    """Bestellung stornieren.  Nur Admin"""
    return await _proxy("PUT", f"{SERVICE_URLS['orders']}/orders/{order_id}/cancel", request)

# ── Notifications ──────────────────────────────────────────────────────────────
@app.get("/api/notifications/stream", tags=["Notifications"])
async def proxy_sse(request: Request, token: str | None = None):
    """
    SSE Live-Stream.
    Token kann als Query-Parameter übergeben werden, weil EventSource
    im Browser keine Custom-Header unterstützt:
      new EventSource('/api/notifications/stream?token=<jwt>')
    """
    # Token aus Query-Param oder Authorization-Header lesen
    raw_token = token
    if not raw_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            raw_token = auth_header[7:]

    if raw_token:
        try:
            jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token abgelaufen")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Ungültiger Token")
    # Kein Token → trotzdem verbinden (SSE ist read-only, kein Sicherheitsrisiko)

    url = f"{SERVICE_URLS['notifications']}/notifications/stream"

    async def event_stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})

# ── Monitoring ─────────────────────────────────────────────────────────────────
@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    return {
        "service": "gateway-service",
        "status":  "ok",
        "uptime_seconds": int(time.time()),
        "routes": list(SERVICE_URLS.keys()),
        "users": {name: data["role"] for name, data in USERS.items()},
    }

@app.get("/health", tags=["Monitoring"])
async def health():
    return {"status": "ok"}