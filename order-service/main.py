import time
import random
import uuid
import structlog
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
import httpx

# ── Structured logging setup ──────────────────────────────────────
# structlog outputs JSON logs — Promtail picks these up and
# sends to Loki with parsed fields (level, service, trace_id, etc.)
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

log = structlog.get_logger().bind(service="order-service")

app = FastAPI(title="Order Service")

# ── Prometheus metrics ────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "order_requests_total",
    "Total HTTP requests to Order Service",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "order_request_duration_seconds",
    "Request latency for Order Service",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
ORDERS_CREATED = Counter(
    "orders_created_total",
    "Total number of orders created",
    ["status"]
)
ACTIVE_ORDERS = Gauge(
    "active_orders_count",
    "Number of currently active orders"
)
INVENTORY_CALL_ERRORS = Counter(
    "order_inventory_call_errors_total",
    "Failed calls from Order Service to Inventory Service"
)
# Requests currently in flight — used by the HPA to scale on load,
# not just total request count.
ACTIVE_REQUESTS = Gauge(
    "order_active_requests",
    "Current number of active requests"
)

# ── In-memory store ───────────────────────────────────────────────
orders: dict = {}

INVENTORY_SERVICE_URL = "http://inventory-service:8001"
NOTIFICATION_SERVICE_URL = "http://notification-service:8002"


# ── Models ────────────────────────────────────────────────────────
class OrderRequest(BaseModel):
    product_id: str
    quantity: int
    customer_id: str


# Returns the route template (e.g. "/orders/{order_id}") instead of
# the literal path, so metric labels don't grow unbounded per order_id.
def get_route_template(request: Request) -> str:
    route = request.scope.get("route")
    return route.path if route is not None else request.url.path


# ── Middleware — logs + metrics every request ─────────────────────
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

    response = None
    ACTIVE_REQUESTS.inc()
    try:
        response = await call_next(request)
        return response
    finally:
        # Runs even if call_next() raises, so the active-requests
        # gauge and metrics/logs never get skipped on a crash.
        ACTIVE_REQUESTS.dec()

        duration = time.time() - start
        endpoint = get_route_template(request)
        status = response.status_code if response is not None else 500

        req_log.info(
            "request_completed",
            status=status,
            duration_ms=round(duration * 1000, 2)
        )

        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status=status
        ).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

        if response is not None:
            response.headers["x-trace-id"] = trace_id


# ── Routes ────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "order-service"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/orders")
async def create_order(order: OrderRequest, request: Request):
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4())[:16])
    order_log = log.bind(
        trace_id=trace_id,
        product_id=order.product_id,
        customer_id=order.customer_id,
        quantity=order.quantity
    )

    order_log.info("order_creation_started")

    # Simulate occasional slow responses
    if random.random() < 0.1:
        delay = random.uniform(1.5, 3.0)
        order_log.warning("simulated_slow_response", delay_seconds=round(delay, 2))
        time.sleep(delay)

    # Simulate occasional failures
    if random.random() < 0.05:
        order_log.error("order_creation_failed", reason="simulated_internal_error")
        ORDERS_CREATED.labels(status="failed").inc()
        raise HTTPException(status_code=500, detail="Internal error processing order")

    order_id = str(uuid.uuid4())[:8]

    # Call inventory service — pass trace_id for correlation
    try:
        order_log.info("calling_inventory_service", inventory_url=INVENTORY_SERVICE_URL)
        async with httpx.AsyncClient(timeout=3.0) as client:
            inv_resp = await client.put(
                f"{INVENTORY_SERVICE_URL}/inventory/reduce",
                json={"product_id": order.product_id, "quantity": order.quantity},
                headers={"x-trace-id": trace_id}
            )
            if inv_resp.status_code != 200:
                INVENTORY_CALL_ERRORS.inc()
                ORDERS_CREATED.labels(status="failed").inc()
                order_log.error(
                    "inventory_check_failed",
                    status_code=inv_resp.status_code,
                    response=inv_resp.text
                )
                raise HTTPException(status_code=400, detail="Insufficient inventory")

        order_log.info("inventory_check_passed")

    except httpx.RequestError as e:
        INVENTORY_CALL_ERRORS.inc()
        ORDERS_CREATED.labels(status="failed").inc()
        order_log.error("inventory_service_unreachable", error=str(e))
        raise HTTPException(status_code=503, detail="Inventory service unavailable")

    # Store order
    orders[order_id] = {
        "order_id": order_id,
        "product_id": order.product_id,
        "quantity": order.quantity,
        "customer_id": order.customer_id,
        "status": "confirmed"
    }
    ACTIVE_ORDERS.set(len(orders))
    ORDERS_CREATED.labels(status="success").inc()

    order_log.info("order_created_successfully", order_id=order_id)

    # Notify — fire and forget, pass trace_id
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{NOTIFICATION_SERVICE_URL}/notify",
                json={
                    "customer_id": order.customer_id,
                    "message": f"Order {order_id} confirmed!"
                },
                headers={"x-trace-id": trace_id}
            )
        order_log.info("notification_sent", order_id=order_id)
    except Exception as e:
        order_log.warning("notification_failed", error=str(e), order_id=order_id)

    return orders[order_id]


@app.get("/orders")
def list_orders():
    log.info("listing_orders", total=len(orders))
    return {"orders": list(orders.values()), "total": len(orders)}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id not in orders:
        log.warning("order_not_found", order_id=order_id)
        raise HTTPException(status_code=404, detail="Order not found")
    return orders[order_id]