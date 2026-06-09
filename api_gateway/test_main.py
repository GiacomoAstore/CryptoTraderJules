import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_compute_daily_performance_no_pool(capsys):
    from main import compute_daily_performance, trade_repo

    # Store original pool and restore it later if needed, though pytest modules reload is clean
    original_pool = trade_repo.pool
    trade_repo.pool = None

    await compute_daily_performance()

    captured = capsys.readouterr()
    assert "DB pool not available for daily performance computation." in captured.out

    trade_repo.pool = original_pool

@pytest.mark.asyncio
async def test_compute_daily_performance_success():
    from main import compute_daily_performance, trade_repo

    # Mocking trade_repo.pool
    trade_repo.pool = MagicMock()

    mock_conn = AsyncMock()
    trade_repo.pool.acquire.return_value.__aenter__.return_value = mock_conn

    # Setup fetch returns
    # 1. total stats
    mock_conn.fetch.side_effect = [
        [{'total_trades': 10, 'total_pnl': 100.5, 'winning_trades': 6}],
        [{'max_loss': -50.2}],
        [{'mean_pnl': 10.05, 'std_pnl': 15.0}]
    ]

    with patch('main.redis.Redis') as mock_redis:
        mock_redis_client = AsyncMock()
        mock_redis.return_value = mock_redis_client

        await compute_daily_performance()

        # Verify db queries were made
        assert mock_conn.fetch.call_count == 3
        mock_conn.execute.assert_called_once()

        # Verify redis publish
        mock_redis_client.publish.assert_called_once()
        args, kwargs = mock_redis_client.publish.call_args
        assert args[0] == "alerts"
        assert "Trades: 10" in args[1]
        assert "PnL: 100.50" in args[1]


@pytest.mark.asyncio
async def test_compute_daily_performance_no_trades(capsys):
    from main import compute_daily_performance, trade_repo

    trade_repo.pool = MagicMock()
    mock_conn = AsyncMock()
    trade_repo.pool.acquire.return_value.__aenter__.return_value = mock_conn

    # Return empty list or a row with total_trades = 0
    mock_conn.fetch.return_value = []

    await compute_daily_performance()

    captured = capsys.readouterr()
    assert "No trades today to compute performance." in captured.out

    # Check that execution stops here and execute/publish are not called
    mock_conn.execute.assert_not_called()

    # also test total_trades = 0 case
    mock_conn.fetch.return_value = [{'total_trades': 0}]

    await compute_daily_performance()

    captured = capsys.readouterr()
    assert "No trades today to compute performance." in captured.out


@pytest.mark.asyncio
async def test_compute_daily_performance_exception(capsys):
    from main import compute_daily_performance, trade_repo

    trade_repo.pool = MagicMock()
    mock_conn = AsyncMock()
    trade_repo.pool.acquire.return_value.__aenter__.return_value = mock_conn

    # Trigger exception
    mock_conn.fetch.side_effect = Exception("Database connection error")

    await compute_daily_performance()

    captured = capsys.readouterr()
    assert "Failed to compute daily performance: Database connection error" in captured.out
