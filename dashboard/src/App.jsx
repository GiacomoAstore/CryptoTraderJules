import { useState, useEffect } from 'react';
import ChartComponent from './ChartComponent';
import './App.css';

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [latestTick, setLatestTick] = useState(null);
  const [trades, setTrades] = useState([]);
  const [metrics, setMetrics] = useState({
    paper_balance: 10000.0, daily_pnl: 0.0, win_rate: 0.0, max_drawdown: 0.0,
    live_daily_pnl: 0.0, live_win_rate: 0.0, live_total_trades: 0
  });
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

  const handleKillSwitch = () => {
    if (window.confirm("ARE YOU SURE? This will halt all new trades globally!")) {
      fetch('http://localhost:8000/api/kill-switch', { method: 'POST' })
        .then(res => res.json())
        .then(data => alert(data.message))
        .catch(err => alert("Failed to trigger Kill Switch!"));
    }
  };

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>CryptoScalper Pro Dashboard</h1>
        <button
          onClick={handleKillSwitch}
          style={{ backgroundColor: '#ff0000', color: 'white', padding: '10px 20px', border: 'none', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer', fontSize: '16px' }}
        >
          🚨 KILL SWITCH
        </button>
      </div>

      <div style={{ display: 'flex', gap: '20px', marginBottom: '20px' }}>
        {/* PAPER METRICS PANE */}
        <div style={{ flex: 1, padding: '15px', backgroundColor: '#222', borderRadius: '8px' }}>
            <h3 style={{ marginTop: 0, color: '#aaa' }}>🧪 Paper Metrics</h3>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <div><small style={{color: '#aaa'}}>Status</small><br/><b style={{ color: isConnected ? 'green' : 'red' }}>{isConnected ? 'Connected' : 'Disconnected'}</b></div>
                <div><small style={{color: '#aaa'}}>Virtual Balance</small><br/><b>${metrics.paper_balance.toFixed(2)}</b></div>
                <div><small style={{color: '#aaa'}}>Paper PnL</small><br/><b style={{color: metrics.daily_pnl >= 0 ? '#00e676' : '#ff5252'}}>${metrics.daily_pnl.toFixed(2)}</b></div>
                <div><small style={{color: '#aaa'}}>Win Rate</small><br/><b>{metrics.win_rate.toFixed(1)}%</b></div>
                <div><small style={{color: '#aaa'}}>Max DD</small><br/><b style={{color: '#ff5252'}}>${metrics.max_drawdown.toFixed(2)}</b></div>
            </div>
        </div>

        {/* LIVE METRICS PANE */}
        <div style={{ flex: 1, padding: '15px', backgroundColor: '#3a2020', border: '1px solid #ff5252', borderRadius: '8px' }}>
            <h3 style={{ marginTop: 0, color: '#ff5252' }}>🔥 LIVE Metrics</h3>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <div><small style={{color: '#aaa'}}>Live Trades Today</small><br/><b>{metrics.live_total_trades}</b></div>
                <div><small style={{color: '#aaa'}}>Real PnL</small><br/><b style={{color: metrics.live_daily_pnl >= 0 ? '#00e676' : '#ff5252'}}>${metrics.live_daily_pnl.toFixed(2)}</b></div>
                <div><small style={{color: '#aaa'}}>Real Win Rate</small><br/><b>{metrics.live_win_rate.toFixed(1)}%</b></div>
                <div>
                    <small style={{color: '#aaa'}}>Slippage (Real - Paper)</small><br/>
                    <b style={{color: (metrics.live_daily_pnl - metrics.daily_pnl) >= 0 ? '#00e676' : '#ff5252'}}>
                        ${(metrics.live_daily_pnl - metrics.daily_pnl).toFixed(2)}
                    </b>
                </div>
            </div>
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