import http from 'k6/http';

export const options = {
  stages: [
    { duration: '1m', target: 20 },    // baseline
    { duration: '2m', target: 40 },   // ramp — should push order_active_requests past threshold
    { duration: '3m', target: 40 },   // sustain — watch HPA scale order-service up
    { duration: '2m', target: 20 },    // ramp down — watch scale-down (after stabilization window)
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    // Don't fail the test run on high error rates — we expect most
    // requests to fail at the inventory-service call right now, since
    // it isn't deployed yet. We're testing scaling behavior, not
    // success rate, at this stage.
    http_req_failed: ['rate<1.0'],
  },
};

export default function () {
  http.post(
    'http://order-service.order-app.svc:8000/orders',
    JSON.stringify({
      product_id: 'PROD-001',
      quantity: 1,
      customer_id: 'k6-load-test',
    }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  // No sleep() — maximizes concurrent in-flight requests per VU,
  // which is what order_active_requests actually measures. Adding
  // sleep() here would reduce real concurrency and make it harder to
  // cross the HPA threshold.
}