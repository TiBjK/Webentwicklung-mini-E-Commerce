# 🛒 Micro-E-Commerce – Projektarbeit (WDS25B)

Ein Microservices-basiertes E-Commerce-System, entwickelt im Rahmen der Vorlesung **Verteilte Systeme und Web Entwicklung** an der DHBW Karlsruhe.

Alle Services sind mit **FastAPI (Python)** implementiert, kommunizieren per **REST/JSON** und werden über **Docker Compose** (lokal) sowie **Kubernetes** (Produktion) betrieben.  
Live-Benachrichtigungen werden über **Server-Sent Events (SSE)** realisiert.  
Das Frontend ist eine **Single Page Application (SPA)** – eine einzige `index.html` mit clientseitigem Routing zwischen Shop-, Checkout- und Admin-View.

---

## 📁 Projektstruktur

```
micro-ecommerce/
├── services/
│   ├── gateway-service/       # Port 8000 – Login (JWT), RBAC & API-Proxy
│   ├── product-service/       # Port 8001 – Produktkatalog
│   ├── inventory-service/     # Port 8002 – Lagerverwaltung & Reservierung
│   ├── order-service/         # Port 8003 – Bestellungen
│   └── notification-service/  # Port 8004 – SSE-Benachrichtigungen
├── frontend/
│   └── index.html             # SPA: Shop, Checkout und Admin in einer Datei
├── docker-compose.yml
├── kubernetes.yaml
└── README.md
```

Jeder Service enthält: `main.py` · `requirements.txt` · `Dockerfile`  
Datenpersistenz: JSON-Dateien in `/app/data` (via Docker Volumes / Kubernetes PVCs)

---

## 🏗️ Architektur

```
Browser (SPA – index.html, clientseitiges Routing via History API)
        │
        ▼  HTTP REST/JSON
┌───────────────────┐
│  Gateway :8000    │  ← Einziger externer Einstiegspunkt
│  JWT-Auth + RBAC  │    Login, Token-Validierung, Rollen-Check
│  API-Proxy        │    Leitet Requests an interne Services weiter
└────────┬──────────┘
         │  HTTP (internes Netz / Kubernetes ClusterIP)
    ┌────┼─────────────────────────────────┐
    ▼    ▼              ▼                  ▼
Product  Inventory    Order          Notification
:8001    :8002        :8003            :8004
  │        ▲            │  Reserve       │
  │        └────────────┘  Stock         │
  │                      │  POST notif.  │
  │                      └──────────────▶│
  │                                      │ SSE-Stream (via Gateway)
  │                                      ▼
JSON-PVC              JSON-PVC       JSON-PVC (dateibasiertes Polling)
```

### Single Page Application (SPA)

Das Frontend (`index.html`) ist als SPA umgesetzt. Es gibt keine separaten HTML-Seiten mehr – alle drei Views (Shop, Checkout, Admin) leben in einer einzigen Datei und werden über JavaScript ein- und ausgeblendet. Die Navigation nutzt die **History API** (`history.pushState`), sodass die URL im Browser aktualisiert wird, ohne die Seite neu zu laden. Der Browser-Zurück-Button funktioniert entsprechend.

Der Router schützt Views rollenbasiert:
- Checkout und Admin sind ohne Login nicht erreichbar
- Der Admin-Link in der Navigation erscheint nur für Nutzer mit der Rolle `admin`
- Nach dem Login landet ein Admin direkt im Admin-Dashboard, ein normaler User im Shop

### Kommunikationsmuster

| Muster | Wo eingesetzt |
|--------|--------------|
| **Synchrones REST** | Gateway → alle Services; Order → Inventory (Reservierung) |
| **Asynchrones REST (fire-and-forget)** | Order → Notification (Fehler werden ignoriert, Bestellung bleibt gültig) |
| **Server-Sent Events (SSE)** | Notification-Service → Browser via Gateway (Live-Updates in Shop und Admin) |

### Authentifizierung & Autorisierung (RBAC)

- Login via `POST /login` → JWT-Token (HS256, 1h TTL)
- Rolle im Token-Payload (`"role": "admin"` | `"user"`)
- Gateway prüft Rolle vor jeder Weiterleitung:
  - `admin`: Vollzugriff (Produkte anlegen/löschen, Bestände setzen, alle Bestellungen einsehen/stornieren)
  - `user`: Nur lesen & Bestellungen aufgeben
- SPA-Router versteckt Admin-Elemente für normale User clientseitig

---

## 🔧 Voraussetzungen

| Tool | Version | Download |
|------|---------|---------|
| **Python** | 3.12+ | https://python.org |
| **Docker Desktop** | aktuell | https://docker.com/products/docker-desktop |
| **kubectl** | aktuell | https://kubernetes.io/docs/tasks/tools/ |
| **minikube** *(für lokales K8s)* | aktuell | https://minikube.sigs.k8s.io/docs/start/ |

---

## 🚀 Lokal starten – Docker Compose

```bash
# Im Hauptordner des Projekts:
docker-compose up --build

# Alle Services stoppen:
docker-compose down

# Volumes (gespeicherte JSON-Daten) komplett löschen:
docker-compose down -v
```

Nach dem Start:

| Seite / Service | URL |
|----------------|-----|
| **SPA (Shop / Checkout / Admin)** | http://localhost:8000/frontend/index.html |
| **Gateway Swagger** | http://localhost:8000/docs |
| Product Swagger | http://localhost:8001/docs |
| Inventory Swagger | http://localhost:8002/docs |
| Order Swagger | http://localhost:8003/docs |
| Notification Swagger | http://localhost:8004/docs |

> **Hinweis:** Der SSE-Stream läuft über den Gateway (`/api/notifications/stream`). Da `EventSource` im Browser keine Custom-Header unterstützt, wird das JWT-Token als Query-Parameter übergeben.

## ☸️ Kubernetes deployen (Docker Desktop)

**Voraussetzung:** Kubernetes in Docker Desktop aktivieren:  
`Docker Desktop` → `Settings` → `Kubernetes` → **"Enable Kubernetes"** → `Apply & Restart`

### 1. Images bauen

```bash
docker build -t micro-ecommerce/gateway-service:latest      ./services/gateway-service
docker build -t micro-ecommerce/product-service:latest      ./services/product-service
docker build -t micro-ecommerce/inventory-service:latest    ./services/inventory-service
docker build -t micro-ecommerce/order-service:latest        ./services/order-service
docker build -t micro-ecommerce/notification-service:latest ./services/notification-service
```

### 2. Deployment

```bash
# Namespace, Secret, PVCs, Deployments, Services anlegen
kubectl apply -f kubernetes.yaml

# ConfigMap mit der SPA befüllen
# --dry-run + apply funktioniert immer, egal ob der ConfigMap schon existiert oder nicht
kubectl create configmap frontend-files --from-file=index.html=./frontend/index.html -n micro-ecommerce --dry-run=client -o yaml | kubectl apply -f -

# Gateway neu starten damit er das Frontend-Volume aufnimmt
kubectl rollout restart deployment/gateway-service -n micro-ecommerce
```

### 3. Frontend öffnen

Warten bis alle Pods laufen (ca. 30 Sekunden), Status prüfen:

```bash
kubectl get pods -n micro-ecommerce
```

Dann Port-Forwarding starten:

```bash
kubectl port-forward -n micro-ecommerce service/gateway-service 8080:80
```

Dann im Browser: http://localhost:8080/frontend/index.html

> **Bei erneutem Deployment** (nach Änderungen) immer denselben Ablauf wiederholen: erst `kubectl delete -f kubernetes.yaml`, dann wieder ab Schritt 1.

### 4. Logs & Debugging

```bash
# Status aller Pods prüfen
kubectl get pods -n micro-ecommerce

# Logs eines Services anzeigen
kubectl logs -n micro-ecommerce deployment/gateway-service -f
```

### 5. Alles entfernen

```bash
kubectl delete -f kubernetes.yaml
```

---

## 🔐 API testen – Schritt-für-Schritt

Unter `http://localhost:8000/docs` (oder `http://localhost:8080/docs` bei Port-Forwarding) erreichbar.

### 1. Login → Token holen

`POST http://localhost:8000/login`
```json
{ "username": "admin", "password": "password" }
```

**Test-Accounts:**

| User | Password | Rolle |
|------|----------|-------|
| `admin` | `password` | Admin (Vollzugriff) |
| `user` | `user123` | User (lesen & bestellen) |

### 2. Token in Swagger eintragen

`http://localhost:8000/docs` → **🔒 Authorize** → `Bearer <token>`

### 3. Produkt anlegen (nur admin)

`POST /api/products`
```json
{
  "name": "Laptop Pro 15",
  "description": "Leistungsstarker Laptop",
  "price": 999.99,
  "category": "electronics"
}
```

### 4. Lagerbestand setzen (nur admin)

`PUT /api/inventory/{product_id}`
```json
{ "quantity": 50 }
```

### 5. Bestellung aufgeben (admin + user)

`POST /api/orders`
```json
{
  "customer_name": "Max Mustermann",
  "customer_email": "max@example.com",
  "items": [
    { "product_id": "<product_id>", "quantity": 2, "unit_price": 999.99 }
  ]
}
```

Der Order-Service reserviert automatisch den Bestand beim Inventory-Service und löst eine Benachrichtigung im Notification-Service aus, die per SSE an alle verbundenen Clients gepusht wird.

### 6. Live-Benachrichtigungen (SSE)

```bash
# Terminal
curl -N http://localhost:8000/api/notifications/stream

# Browser-Konsole
const es = new EventSource('http://localhost:8000/api/notifications/stream');
es.onmessage = e => console.log(JSON.parse(e.data));
```

---

## 📊 Monitoring

Jeder Service stellt `/health` und `/metrics` bereit:

| Service | `/metrics` liefert |
|---------|-------------------|
| Gateway | Status, verfügbare Routes, registrierte User-Rollen |
| Product | Anzahl Produkte, Uptime |
| Inventory | Anzahl Artikel, Gesamt-Lagereinheiten, Uptime |
| Order | Gesamtbestellungen, Aufschlüsselung nach Status, Uptime |
| Notification | Anzahl Benachrichtigungen, Poll-Intervall, Uptime |

**Business-KPIs** werden im Admin-View der SPA visualisiert:
- Anzahl Produkte, Bestellungen, Lagereinheiten
- Gesamtumsatz (ohne stornierte Bestellungen)

Kubernetes nutzt `/health` als **Liveness Probe** für automatischen Neustart bei Fehlern.

---

## 🐛 Troubleshooting

**Services starten nicht:**
```bash
docker-compose logs <service-name>
```

**„Connection refused" zwischen Services:**
```bash
docker-compose ps   # Alle Container laufen?
```

**Token abgelaufen:**  
JWT läuft nach 1 Stunde ab → neu einloggen.

**Kubernetes: `ImagePullBackOff`:**  
`eval $(minikube docker-env)` muss **im selben Terminal** vor dem `docker build` ausgeführt werden.

**Kubernetes: PVC bleibt `Pending`:**
```bash
kubectl describe pvc -n micro-ecommerce
kubectl get storageclass   # Standard-StorageClass vorhanden?
```

**SSE-Stream bricht ab:**  
Der Client versucht automatisch alle 3 Sekunden eine neue Verbindung. Heartbeat alle 15 Sekunden hält die Verbindung durch Proxies offen.

**Frontend zeigt „Fehler beim Laden":**  
CORS ist mit `allow_origins=["*"]` konfiguriert. Browser-Cache leeren oder Inkognito-Modus nutzen.

**SPA zeigt falsche View nach Reload:**  
Der Gateway muss alle Routen auf `index.html` weiterleiten. Bei direktem Datei-Aufruf über `file://` funktioniert die History API nicht – immer über den Gateway (`http://localhost:8000`) aufrufen.
