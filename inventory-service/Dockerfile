FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home \
    --uid 10001 \
    --shell /usr/sbin/nologin \
    appuser

COPY main.py .

USER 10001

EXPOSE 8001

CMD ["opentelemetry-instrument", "--traces_exporter", "otlp", "--metrics_exporter", "none", "--exporter_otlp_endpoint", "http://otel-collector.monitoring:4317", "--service_name", "inventory-service", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
