FROM python:3.11-slim

WORKDIR /app

COPY live_executor/requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# Copy common directory first
COPY common ./common

COPY live_executor/main.py .

CMD ["python", "main.py"]
