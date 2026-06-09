import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock, AsyncMock
from api_gateway.main import app, trade_repo

@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")

@pytest.mark.asyncio
async def test_get_metrics_happy_path(client):
    with patch("api_gateway.main.redis.Redis") as mock_redis_class:
        mock_redis = MagicMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.get = AsyncMock(return_value=b"15000.0")
        mock_redis.aclose = AsyncMock()

        # Mock the db pool
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Mock fetch returns
        async def mock_fetch(query):
            if "close_reason != 'LIVE_MARKET'" in query:
                return [{"total_trades": 10, "total_pnl": 500.0, "winning_trades": 6, "max_loss": -50.0}]
            elif "close_reason = 'LIVE_MARKET'" in query:
                return [{"total_trades": 5, "total_pnl": 200.0, "winning_trades": 3}]
            return []

        mock_conn.fetch = AsyncMock(side_effect=mock_fetch)

        with patch("api_gateway.main.trade_repo.pool", new=mock_pool):
            response = await client.get("/api/metrics")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["metrics"]["paper_balance"] == 15000.0
            assert data["metrics"]["daily_pnl"] == 500.0
            assert data["metrics"]["win_rate"] == 60.0
            assert data["metrics"]["max_drawdown"] == -50.0
            assert data["metrics"]["live_total_trades"] == 5
            assert data["metrics"]["live_daily_pnl"] == 200.0
            assert data["metrics"]["live_win_rate"] == 60.0

@pytest.mark.asyncio
async def test_get_metrics_db_pool_none(client):
    with patch("api_gateway.main.redis.Redis") as mock_redis_class:
        mock_redis = MagicMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.get = AsyncMock(return_value=b"12000.0")
        mock_redis.aclose = AsyncMock()

        with patch("api_gateway.main.trade_repo.pool", new=None):
            response = await client.get("/api/metrics")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["metrics"]["paper_balance"] == 12000.0
            assert data["metrics"]["daily_pnl"] == 0.0
            assert data["metrics"]["win_rate"] == 0.0
            assert data["metrics"]["max_drawdown"] == 0.0
            assert data["metrics"]["live_total_trades"] == 0
            assert data["metrics"]["live_daily_pnl"] == 0.0
            assert data["metrics"]["live_win_rate"] == 0.0

@pytest.mark.asyncio
async def test_get_metrics_redis_failure(client):
    with patch("api_gateway.main.redis.Redis") as mock_redis_class:
        mock_redis = MagicMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection error"))
        mock_redis.aclose = AsyncMock()

        # Mock the db pool
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Mock fetch returns
        async def mock_fetch(query):
            if "close_reason != 'LIVE_MARKET'" in query:
                return [{"total_trades": 10, "total_pnl": 500.0, "winning_trades": 6, "max_loss": -50.0}]
            elif "close_reason = 'LIVE_MARKET'" in query:
                return [{"total_trades": 5, "total_pnl": 200.0, "winning_trades": 3}]
            return []

        mock_conn.fetch = AsyncMock(side_effect=mock_fetch)

        with patch("api_gateway.main.trade_repo.pool", new=mock_pool):
            response = await client.get("/api/metrics")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            # Should fallback to default balance
            assert data["metrics"]["paper_balance"] == 10000.0
            assert data["metrics"]["daily_pnl"] == 500.0

@pytest.mark.asyncio
async def test_get_metrics_db_fetch_exception(client):
    with patch("api_gateway.main.redis.Redis") as mock_redis_class:
        mock_redis = MagicMock()
        mock_redis_class.return_value = mock_redis
        mock_redis.get = AsyncMock(return_value=b"11000.0")
        mock_redis.aclose = AsyncMock()

        # Mock the db pool
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetch = AsyncMock(side_effect=Exception("DB Query failed"))

        with patch("api_gateway.main.trade_repo.pool", new=mock_pool):
            response = await client.get("/api/metrics")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["metrics"]["paper_balance"] == 11000.0
            assert data["metrics"]["daily_pnl"] == 0.0
            assert data["metrics"]["win_rate"] == 0.0
            assert data["metrics"]["max_drawdown"] == 0.0
            assert data["metrics"]["live_total_trades"] == 0
            assert data["metrics"]["live_daily_pnl"] == 0.0
            assert data["metrics"]["live_win_rate"] == 0.0
