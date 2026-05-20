FROM python:3.11-slim

WORKDIR /app

COPY reporter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY reporter/ .
COPY scripts/phase1 /app/scripts/phase1
COPY shared_config /app/shared_config

CMD ["python", "main.py"]
