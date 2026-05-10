import { useState, useEffect } from 'react';
import ChartComponent from './ChartComponent';
import './App.css';

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [latestTick, setLatestTick] = useState(null);
  const [trades, setTrades] = useState([]);
  const [symbol] = useState('btcusdt');
  const [botEnabled, setBotEnabled] = useState(false);
  const [portfolio, setPortfolio] = useState({
    total_capital: 0,
    net_profit: 0,
    usdt_balance: 0,
    holdings: []
  });
  const [realPortfolio, setRealPortfolio] = useState({
    total_value_usdt: 0,
    balances: []
  });
  
  const [token, setToken] = useState(null);

  const fetchTrades = (jwt) => {
    fetch('http://localhost:8000/api/trades', {
      headers: { 'Authorization': `Bearer ${jwt}` }
    })
      .then((res) => res.json())
      .then((data) => {
        if (data && data.trades) {
          setTrades(data.trades);
        }
      })
      .catch((err) => console.error("Failed to fetch initial trades:", err));
  };

  const fetchRealPortfolio = (jwt) => {
    fetch('http://localhost:8000/api/portfolio/real', {
      headers: { 'Authorization': `Bearer ${jwt}` }
    })
      .then((res) => res.json())
      .then((data) => {
        if (data && data.status === 'ok') {
          setRealPortfolio(data);
        }
      })
      .catch((err) => console.error("Failed to fetch real portfolio:", err));
  };

  useEffect(() => {
    // 1. Authenticate to get real JWT token
    const formData = new URLSearchParams();
    formData.append('username', 'admin');
    formData.append('password', 'admin');

    fetch('http://localhost:8000/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData.toString()
    })
      .then(res => res.json())
      .then(loginData => {
        if (loginData.access_token) {
          setToken(loginData.access_token);
          fetchTrades(loginData.access_token);
          fetchRealPortfolio(loginData.access_token);
        }
      })
      .catch(err => console.error("Login failed:", err));

    // Connect to WebSocket
    const ws = new WebSocket('ws://localhost:8000/ws/live');


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
        } else if (msg.channel === 'system' && msg.data.bot_enabled !== undefined) {
          setBotEnabled(msg.data.bot_enabled);
        } else if (msg.channel === 'portfolio') {
          setPortfolio(msg.data);
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

  const toggleBot = async () => {
    try {
      const newState = !botEnabled;
      const response = await fetch(`http://localhost:8000/api/bot/toggle?enabled=${newState}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        setBotEnabled(newState);
      }
    } catch (err) {
      console.error("Failed to toggle bot", err);
    }
  };

  return (
    <div className="dashboard-container">
      <div className="header glass-panel">
        <div>
          <h1>CryptoScalper Pro</h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{
              width: '10px', height: '10px', borderRadius: '50%',
              background: isConnected ? 'var(--success)' : 'var(--danger)',
              boxShadow: isConnected ? '0 0 10px var(--success)' : 'none'
            }}></div>
            <span>{isConnected ? 'System Online' : 'System Offline'}</span>
          </div>
        </div>
        
        <div>
          <button 
            className={`btn-toggle ${botEnabled ? 'active' : 'inactive'}`}
            onClick={toggleBot}
          >
            {botEnabled ? 'BOT ACTIVE (LIVE)' : 'BOT PAUSED'}
          </button>
        </div>
      </div>

      <div className="portfolio-dashboard" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '32px' }}>
        <div className="glass-panel stat-card">
          <h3>Total Capital</h3>
          <div className="stat-value" style={{ fontSize: '2.5rem', fontWeight: 'bold', color: 'var(--text)' }}>
            ${Number(portfolio.total_capital).toFixed(2)}
          </div>
          <div className="stat-label" style={{ color: 'var(--text-muted)' }}>
            Available USDT: ${Number(portfolio.usdt_balance).toFixed(2)}
          </div>
        </div>

        <div className="glass-panel stat-card">
          <h3>Net Profit / Loss</h3>
          <div className="stat-value" style={{ 
            fontSize: '2.5rem', 
            fontWeight: 'bold', 
            color: portfolio.net_profit >= 0 ? 'var(--success)' : 'var(--danger)'
          }}>
            {portfolio.net_profit >= 0 ? '+' : ''}${Number(portfolio.net_profit).toFixed(2)}
          </div>
        </div>

        <div className="glass-panel stat-card" style={{ gridColumn: '1 / -1' }}>
          <h3>Active Holdings</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Quantity</th>
                <th>Current Price</th>
                <th>Total Value (USD)</th>
              </tr>
            </thead>
            <tbody>
              {portfolio.holdings.map((h, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{h.symbol}</td>
                  <td>{Number(h.quantity).toFixed(4)}</td>
                  <td>${Number(h.current_price).toFixed(2)}</td>
                  <td style={{ color: 'var(--success)' }}>${Number(h.value).toFixed(2)}</td>
                </tr>
              ))}
              {portfolio.holdings.length === 0 && (
                <tr>
                  <td colSpan="4" style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)' }}>
                    No active positions.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="portfolio-dashboard" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '32px' }}>
        <div className="glass-panel stat-card" style={{ gridColumn: '1 / -1', background: 'rgba(20, 25, 40, 0.7)', border: '1px solid var(--primary)' }}>
          <h3 style={{ color: 'var(--primary)' }}>Real Binance Portfolio</h3>
          <div style={{ marginBottom: '16px' }}>
            <span style={{ fontSize: '1rem', color: 'var(--text-muted)' }}>Total Value: </span>
            <span style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--text)' }}>
              ${Number(realPortfolio.total_value_usdt).toFixed(2)}
            </span>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Free</th>
                <th>Locked</th>
                <th>Total Value (USD)</th>
              </tr>
            </thead>
            <tbody>
              {realPortfolio.balances && realPortfolio.balances.map((b, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{b.asset}</td>
                  <td>{Number(b.free).toFixed(4)}</td>
                  <td>{Number(b.locked).toFixed(4)}</td>
                  <td style={{ color: 'var(--success)' }}>${Number(b.value_usdt).toFixed(2)}</td>
                </tr>
              ))}
              {(!realPortfolio.balances || realPortfolio.balances.length === 0) && (
                <tr>
                  <td colSpan="4" style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)' }}>
                    Loading real portfolio or no assets found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <h2 style={{ marginBottom: '16px' }}>Live Market Analysis</h2>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '32px' }}>
        <div className="glass-panel">
          <ChartComponent symbol="BTCUSDT" tickData={latestTick} />
        </div>
        <div className="glass-panel">
          <ChartComponent symbol="ETHUSDT" tickData={latestTick} />
        </div>
        <div className="glass-panel">
          <ChartComponent symbol="BNBUSDT" tickData={latestTick} />
        </div>
        <div className="glass-panel">
          <ChartComponent symbol="SOLUSDT" tickData={latestTick} />
        </div>
        <div className="glass-panel" style={{ gridColumn: '1 / -1' }}>
          <ChartComponent symbol="XRPUSDT" tickData={latestTick} />
        </div>
      </div>

      <div className="glass-panel">
        <h2>Recent Execution Log</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Executed Price</th>
              <th>Quantity</th>
              <th>Fee</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade, i) => {
              const tradeSymbol = trade.symbol || (trade.order && trade.order.symbol);
              const side = trade.side || (trade.order && trade.order.type);
              const price = trade.executed_price || trade.price || (trade.order && trade.order.price);
              const quantity = trade.quantity || (trade.order && trade.order.quantity);
              
              return (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{tradeSymbol}</td>
                  <td>
                    <span className={side === 'BUY' ? 'tag-buy' : 'tag-sell'}>
                      {side}
                    </span>
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '1.1rem' }}>${Number(price).toFixed(2)}</td>
                  <td>{quantity || 'N/A'}</td>
                  <td style={{ color: 'var(--text)' }}>{trade.fee ? `$${Number(trade.fee).toFixed(4)}` : '-'}</td>
                </tr>
              );
            })}
            {trades.length === 0 && (
              <tr>
                <td colSpan="5" style={{ textAlign: 'center', padding: '32px', color: 'var(--text)' }}>
                  No trades executed yet. The algorithm is waiting for optimal conditions.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default App;