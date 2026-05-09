# CryptoScalper Pro

CryptoScalper Pro is an algorithmic trading platform designed to execute high-frequency scalping strategies on Binance. It captures micro-price movements by constantly monitoring the market 24/7.

## Architecture

The system is built as a microservice architecture using Docker Compose. It features:
*   **Data Ingestion Service:** Connects to Binance WebSockets, normalizes ticks, and publishes them to Redis.
*   **Signal Engine:** Implements the Strategy Pattern to analyze ticks and generate buy/sell signals.
*   **Risk Manager:** Implements the Circuit Breaker Pattern to evaluate signals against risk thresholds.
*   **Order Executor:** Implements the Command Pattern to execute approved orders.
*   **API Gateway:** A FastAPI service implementing the Repository Pattern to expose REST endpoints and push live data to the frontend via WebSockets.
*   **Dashboard:** A React application (powered by Vite) displaying live TradingView charts and a list of executed trades.
*   **TimescaleDB & Redis:** Used for time-series persistence and event-driven pub/sub communication, respectively.

## How to Run

1.  **Environment Variables:** You MUST create an environment file before starting the cluster. Copy the example file:
    ```bash
    cp .env.example .env
    ```
    *Open `.env` and fill in your Binance API keys and other configuration as needed.*

2.  **Start the Cluster:** Bring up all 8 containers using Docker Compose:
    ```bash
    docker compose up --build
    ```

3.  **View Dashboard:** Once the services have started, open your browser and navigate to `http://localhost:3000`.

## Important Note

High-frequency algorithmic trading involves significant risk. Always test in **PAPER** trading mode before using real funds.
