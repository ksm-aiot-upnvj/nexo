from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "nexo_http_requests_total",
    "HTTP requests handled by the Nexo gateway.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "nexo_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)
WEBHOOK_DELIVERIES = Counter(
    "nexo_github_webhook_deliveries_total",
    "GitHub webhook deliveries by processing outcome.",
    ("outcome",),
)
OUTBOX_JOBS = Gauge(
    "nexo_webhook_outbox_jobs",
    "Webhook outbox jobs grouped by state.",
    ("status",),
)
OUTBOX_RETRIES = Counter(
    "nexo_webhook_outbox_retries_total",
    "Webhook outbox jobs scheduled for retry.",
)
OUTBOX_FAILURES = Counter(
    "nexo_webhook_outbox_terminal_failures_total",
    "Webhook outbox jobs that exhausted their retry attempts.",
)
LLM_REQUESTS = Counter(
    "nexo_llm_requests_total",
    "LLM completion requests by outcome.",
    ("outcome",),
)
LLM_REQUEST_DURATION = Histogram(
    "nexo_llm_request_duration_seconds",
    "LLM completion request duration in seconds.",
)
