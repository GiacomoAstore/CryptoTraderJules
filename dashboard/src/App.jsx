import { useState, useEffect } from 'react';
import ChartComponent from './ChartComponent';
import './App.css';

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [latestTick, setLatestTick] = useState(null);
  const [trades, setTrades] = useState([]);
  const [symbol] = useState('btcusdt');

  useEffect(() => {
    // Initial fetch of trades via REST API
    fetch('http://localhost:8000/api/trades')
      .then((res) => res.json())
      .then((data) => {
        if (data && data.trades) {
          setTrades(data.trades);
        }
      })
      .catch((err) => console.error("Failed to fetch initial trades:", err));

    // Connect to WebSocket
    const ws = new WebSocket('ws://localhost:8000/ws');

    ws.onopen = () => {
      setIsConnected(true);
      console.log('Connected to API Gateway WebSocket');
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.channel.startsWith('ticks:')) {
          setLatestTick(msg.data);
        } else if (msg.channel === 'executed_trades') {
          // Prepend new trade to the list
          setTrades(prev => [msg.data, ...prev].slice(0, 50));
        }
      } catch (err) {
        console.error("Failed to parse websocket message", err);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      console.log('Disconnected from API Gateway WebSocket');
    };

    return () => {
      ws.close();
    };
  }, []);

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '20px' }}>
      <h1>CryptoScalper Pro Dashboard</h1>
      <div style={{ marginBottom: '20px' }}>
        Status: <span style={{ color: isConnected ? 'green' : 'red', fontWeight: 'bold' }}>
          {isConnected ? 'Connected' : 'Disconnected'}
        </span>
      </div>

      <ChartComponent symbol={symbol} tickData={latestTick} />

      <div style={{ marginTop: '40px' }}>
        <h2>Recent Trades</h2>
        <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #444' }}>
              <th>Symbol</th>
              <th>Side</th>
              <th>Price</th>
              <th>Quantity</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade, i) => (
              <tr key={i} style={{ borderBottom: '1px solid #333' }}>
                <td>{trade.symbol || (trade.order && trade.order.symbol)}</td>
                <td style={{ color: (trade.side || (trade.order && trade.order.type)) === 'BUY' ? '#00e676' : '#ff5252' }}>
                  {trade.side || (trade.order && trade.order.type)}
                </td>
                <td>${trade.price || (trade.order && trade.order.price)}</td>
                <td>{trade.quantity || (trade.order && trade.order.quantity) || 'N/A'}</td>
              </tr>
            ))}
            {trades.length === 0 && (
              <tr>
                <td colSpan="4" style={{ textAlign: 'center', padding: '10px' }}>No trades executed yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default App;