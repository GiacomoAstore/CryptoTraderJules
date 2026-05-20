# CryptoScalper Pro

CryptoScalper Pro is an algorithmic trading platform designed to execute high-frequency scalping strategies on Binance. It captures micro-price movements by constantly monitoring the market 24/7.

**Current mode:** paper trading simulation (no live orders on the exchange).

## Architecture

The system is built as a microservice architecture using Docker Compose:

| Service | Role |
|---------|------|
| `data_ingestion` | Binance WebSocket → normalized ticks on Redis |
| `signal_engine` | Multi-strategy consensus + A/B variants |
| `risk_manager` | Sizing, circuit breaker, fee checks |
| `order_executor` | Paper fill engine (SL/TP/timeout) |
| `api_gateway` | REST + WebSocket + DB migrations (Alembic) |
| `dashboard` | React UI (Vite) |
| `reporter` | Daily PnL report + Telegram |
| `telegram_alerter` | Trade/system alerts |
| `llm_optimizer` | Optional Groq-based config tuning |
| `redis` | Pub/Sub bus |
| `timescaledb` | Trade history + positions |

## Ports (host)

| Port | Service |
|------|---------|
| 3000 | Dashboard (nginx) |
| 8000 | API Gateway |
| 6379 | Redis |
| 5432 | TimescaleDB |

## How to Run

1. Ensure a `.env` file exists in the project root (API keys, `JWT_SECRET`, `ADMIN_PASSWORD`, DB credentials).
2. Start the stack:
   ```bash
   docker compose up --build
   ```
3. Open the dashboard: `http://localhost:3000`
4. **Start the bot** from the UI (or `POST /api/bot/start`) — trading is stopped by default.

### Dashboard API URL (optional)

Set in `.env` before building the dashboard image:

```env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
ADMIN_PASSWORD=your-password
```

Then rebuild: `docker compose build dashboard`

## Database schema

- **Bootstrap:** `init_db.sql` only enables the TimescaleDB extension.
- **Tables:** managed by Alembic in `api_gateway/alembic/` (including `trades`, `orders`, `positions`).

On first start, `api_gateway` runs `alembic upgrade head`.

## Tests

Unit tests (no Docker required):

```bash
pip install -r tests/requirements.txt
pytest tests/ -m "not integration"
```

Integration tests (Redis + full stack):

```bash
docker compose up -d
INTEGRATION_STACK=1 pytest tests/integration/ -m integration
```

Smoke test API (stack running):

```bash
python test_apis.py
```

## Important Note

High-frequency algorithmic trading involves significant risk. Always test in **PAPER** mode before using real funds. The dashboard labels paper vs read-only Binance account explicitly.
