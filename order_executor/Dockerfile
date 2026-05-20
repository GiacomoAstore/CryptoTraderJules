FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

COPY main.py binance_rest.py exchange_rules.py live_engine.py ./

CMD ["python", "main.py"]
