import logging
import os
import aiohttp

logger = logging.getLogger(__name__)

async def validate_historical_performance(redis_client=None) -> bool:
    logger.info("Validating historical paper trading performance before allowing LIVE execution...")
    # Fetch metrics from API Gateway / Redis
    # Minimum requirements for live trading: Win Rate > 40%, Max Drawdown < -500
    try:
        async with aiohttp.ClientSession() as session:
            # We assume api_gateway is running on the docker network as 'api_gateway'
            gateway_url = os.getenv("API_GATEWAY_URL", "http://api_gateway:8000")
            async with session.get(f"{gateway_url}/api/metrics") as response:
                if response.status == 200:
                    data = await response.json()
                    metrics = data.get("metrics", {})
                    win_rate = metrics.get("win_rate", 0)
                    max_drawdown = metrics.get("max_drawdown", 0)

                    # NOTE: A real system might look at 2 weeks of aggregated data.
                    # For this step, we use the metrics endpoint.
                    logger.info(f"Historical Metrics - Win Rate: {win_rate}%, Max DD: {max_drawdown}")

                    if win_rate < 40.0:
                        logger.critical("Validation Gate Failed: Win Rate below 40%.")
                        return False
                    if max_drawdown < -500.0:
                        logger.critical("Validation Gate Failed: Max Drawdown too severe.")
                        return False

                    logger.info("Validation Gate Passed. System is clear for LIVE trading.")
                    return True
                else:
                    logger.error(f"Failed to fetch metrics from API Gateway: {response.status}")
                    return False
    except Exception as e:
        logger.error(f"Error during validation gate check: {e}")
        return False
