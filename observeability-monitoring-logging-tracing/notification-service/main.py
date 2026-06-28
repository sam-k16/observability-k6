import time
import random
import uuid
import structlog
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

# ── Structured logging ────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(10),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger().bind(service="notification-service")

app = FastAPI(title="Notification Service")

# ── Prometheus metrics ────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "notification_requests_total",
    "Total HTTP requests to Notification Service",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "notification_request_duration_seconds",
    "Request latency for Notification Service",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
NOTIFICATIONS_SENT = Counter(
    "notifications_sent_total",
    "Total notifications sent",
    ["channel", "status"]
)
NOTIFICATION_QUEUE_SIZE = Gauge(
    "notification_queue_size",
    "Number of notifications currently queued"
)
FAILED_DELIVERIES = Counter(
    "notification_failed_deliveries_total",
    "Total failed notification delivery attempts"
)
# Defined for parity with the other services / future use. Not wired
# into the middleware since NOTIFICATION_QUEUE_SIZE already covers
# backlog tracking for this service.
ACTIVE_REQUESTS = Gauge(
    "notification_active_requests",
    "Current number of active notification requests"
)

# ── In-memory log ─────────────────────────────────────────────────
notifications: list = []
queue_size = 0


# ── Models ────────────────────────────────────────────────────────
class NotificationRequest(BaseModel):
    customer_id: str
    message: str
    channel: str = "email"


# Returns the route template instead of the literal path, to keep
# metric label cardinality bounded if path params are added later.
def get_route_template(request: Request) -> str:
    route = request.scope.get("route")
    return route.path if route is not None else request.url.path


# ── Middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4())[:16])
    start = time.time()

    req_log = log.bind(
        trace_id=trace_id,
        method=request.method,
        path=request.url.path
    )
    req_log.info("request_started")

    response = await call_next(request)
    duration = time.time() - start

    endpoint = get_route_template(request)

    req_log.info(
        "request_completed",
        status=response.status_code,
        duration_ms=round(duration * 1000, 2)
    )

    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code
    ).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

    response.headers["x-trace-id"] = trace_id
    return response


# ── Routes ────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "notification-service"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
def send_notification(req: NotificationRequest, request: Request):
    global queue_size
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4())[:16])

    notif_log = log.bind(
        trace_id=trace_id,
        customer_id=req.customer_id,
        channel=req.channel
    )

    queue_size += 1
    NOTIFICATION_QUEUE_SIZE.set(queue_size)
    notif_log.info("notification_queued", queue_size=queue_size)

    time.sleep(random.uniform(0.05, 0.2))

    # Simulate 8% failure rate
    if random.random() < 0.08:
        queue_size = max(0, queue_size - 1)
        NOTIFICATION_QUEUE_SIZE.set(queue_size)
        FAILED_DELIVERIES.inc()
        NOTIFICATIONS_SENT.labels(channel=req.channel, status="failed").inc()
        notif_log.error(
            "notification_delivery_failed",
            reason="simulated_delivery_failure",
            channel=req.channel
        )
        raise HTTPException(status_code=500, detail="Notification delivery failed")

    record = {
        "customer_id": req.customer_id,
        "message": req.message,
        "channel": req.channel,
        "delivered": True
    }
    notifications.append(record)
    queue_size = max(0, queue_size - 1)
    NOTIFICATION_QUEUE_SIZE.set(queue_size)
    NOTIFICATIONS_SENT.labels(channel=req.channel, status="success").inc()

    notif_log.info(
        "notification_delivered",
        channel=req.channel,
        message=req.message[:50]
    )

    return {"status": "sent", "customer_id": req.customer_id}


@app.get("/notifications")
def list_notifications():
    log.info("listing_notifications", total=len(notifications))
    return {"notifications": notifications, "total": len(notifications)}