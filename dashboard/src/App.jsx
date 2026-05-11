import { useState, useEffect } from 'react';
import ChartComponent from './ChartComponent';
import './App.css';

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [latestTick, setLatestTick] = useState(null);
  const [trades, setTrades] = useState([]);
  const [metrics, setMetrics] = useState({ paper_balance: 10000.0, daily_pnl: 0.0, win_rate: 0.0, max_drawdown: 0.0 });
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

    // Initial fetch of metrics via REST API
    fetch('http://localhost:8000/api/metrics')
      .then((res) => res.json())
      .then((data) => {
        if (data && data.metrics) {
          setMetrics(data.metrics);
        }
      })
      .catch((err) => console.error("Failed to fetch initial metrics:", err));

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
          // Refresh metrics since a trade executed
          fetch('http://localhost:8000/api/metrics')
            .then((res) => res.json())
            .then((data) => {
              if (data && data.metrics) {
                setMetrics(data.metrics);
              }
            }).catch(() => {});
        } else if (msg.channel === 'paper:balance_updates') {
            setMetrics(prev => ({ ...prev, paper_balance: msg.data.balance }));
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

      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '20px', padding: '15px', backgroundColor: '#222', borderRadius: '8px' }}>
        <div>
            Status: <span style={{ color: isConnected ? 'green' : 'red', fontWeight: 'bold' }}>
            {isConnected ? 'Connected' : 'Disconnected'}
            </span>
        </div>
        <div style={{ display: 'flex', gap: '20px' }}>
            <div><small style={{color: '#aaa'}}>Paper Balance</small><br/><b>${metrics.paper_balance.toFixed(2)}</b></div>
            <div><small style={{color: '#aaa'}}>Daily PnL</small><br/><b style={{color: metrics.daily_pnl >= 0 ? '#00e676' : '#ff5252'}}>${metrics.daily_pnl.toFixed(2)}</b></div>
            <div><small style={{color: '#aaa'}}>Win Rate</small><br/><b>{metrics.win_rate.toFixed(1)}%</b></div>
            <div><small style={{color: '#aaa'}}>Max DD</small><br/><b style={{color: '#ff5252'}}>${metrics.max_drawdown.toFixed(2)}</b></div>
        </div>
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