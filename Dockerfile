FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY arista_exporter /app/arista_exporter

EXPOSE 9805

ENTRYPOINT ["python", "-m", "arista_exporter.exporter", "-c", "arista_exporter/config.yaml"]
