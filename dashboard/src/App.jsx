import { useState, useEffect } from 'react';
import ChartComponent from './ChartComponent';
import DbViewer from './DbViewer';
import { apiUrl, wsUrl } from './api';
import './App.css';

const DEFAULT_SYMBOLS = [
  'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'SHIBUSDT', 'AVAXUSDT', 'DOTUSDT',
  'LINKUSDT', 'TRXUSDT', 'LTCUSDT', 'BCHUSDT',
  'UNIUSDT', 'XLMUSDT', 'NEARUSDT', 'ATOMUSDT', 'APTUSDT'
];

function App() {
  const [currentTab, setCurrentTab] = useState('dashboard');
  const [activeCharts, setActiveCharts] = useState(['BTCUSDT', 'ETHUSDT', 'SOLUSDT']);
  const [availableSymbols, setAvailableSymbols] = useState(DEFAULT_SYMBOLS);
  const [isConnected, setIsConnected] = useState(false);
  const [latestTick, setLatestTick] = useState(null);
  const [trades, setTrades] = useState([]);
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
  const [authError, setAuthError] = useState(null);
  const [reportStatus, setReportStatus] = useState(null);

  const fetchBotStatus = (jwt) => {
    fetch(apiUrl('/api/bot/status'), {
      headers: { 'Authorization': `Bearer ${jwt}` }
    })
      .then((res) => res.json())
      .then((data) => {
        if (data?.status) {
          setBotEnabled(data.status === 'running');
        }
      })
      .catch((err) => console.error("Failed to fetch bot status:", err));
  };

  const fetchTrades = (jwt) => {
    fetch(apiUrl('/api/trades'), {
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
    fetch(apiUrl('/api/portfolio/real'), {
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

  const fetchSymbols = (jwt) => {
    fetch(apiUrl('/api/symbols'), {
      headers: { 'Authorization': `Bearer ${jwt}` }
    })
      .then((res) => res.json())
      .then((data) => {
        const symbols = (data?.symbols || []).map((s) => s.symbol).filter(Boolean);
        if (symbols.length > 0) {
          setAvailableSymbols(symbols);
        }
      })
      .catch((err) => console.error("Failed to fetch symbols:", err));
  };

  useEffect(() => {
    const formData = new URLSearchParams();
    formData.append('username', 'admin');
    formData.append('password', import.meta.env.VITE_ADMIN_PASSWORD || 'admin');

    fetch(apiUrl('/api/login'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData.toString()
    })
      .then(async (res) => {
        const loginData = await res.json();
        if (!res.ok) {
          throw new Error(loginData.detail || `Login failed (${res.status})`);
        }
        return loginData;
      })
      .then(loginData => {
        if (loginData.access_token) {
          setAuthError(null);
          setToken(loginData.access_token);
          fetchTrades(loginData.access_token);
          fetchRealPortfolio(loginData.access_token);
          fetchBotStatus(loginData.access_token);
          fetchSymbols(loginData.access_token);

          const ws = new WebSocket(wsUrl(`/ws/live?token=${loginData.access_token}`));

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
                setTrades(prev => [msg.data, ...prev].slice(0, 50));
              } else if (msg.channel === 'system' && msg.data.bot_status !== undefined) {
                setBotEnabled(msg.data.bot_status === 'running');
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

          return () => ws.close();
        }
      })
      .catch(err => {
        console.error("Login failed:", err);
        setAuthError(
          'Login fallito: la password nel build della dashboard non coincide con ADMIN_PASSWORD nel .env. ' +
          'Esegui: docker compose build dashboard --no-cache && docker compose up -d dashboard'
        );
      });

  }, []);


  const toggleBot = async () => {
    try {
      const newState = !botEnabled;
      const response = await fetch(apiUrl(`/api/bot/toggle?enabled=${newState}`), {
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

  const sendMorningReport = async () => {
    if (!token) {
      setReportStatus('Token mancante. Riprova più tardi.');
      return;
    }
    setReportStatus('Richiesta report in corso...');
    try {
      const response = await fetch(apiUrl('/api/report/send'), {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || response.statusText);
      }
      setReportStatus('Report Telegram richiesto. Controlla il canale.');
    } catch (err) {
      console.error('Failed to request morning report', err);
      setReportStatus(`Errore invio report: ${err.message || err}`);
    }
  };

  const toggleChart = (symbol) => {
    setActiveCharts(prev => {
      if (prev.includes(symbol)) {
        return prev.filter(s => s !== symbol);
      }
      if (prev.length >= 3) {
        return [...prev.slice(1), symbol];
      }
      return [...prev, symbol];
    });
  };

  if (currentTab === 'db_viewer' && token) {
    return <DbViewer token={token} onBack={() => setCurrentTab('dashboard')} />;
  }

  return (
    <div className="dashboard-container">
      <div className="header glass-panel">
        <div>
          <h1>CryptoScalper Pro</h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <span className="mode-badge mode-paper">PAPER TRADING</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{
                width: '10px', height: '10px', borderRadius: '50%',
                background: isConnected ? 'var(--success)' : 'var(--danger)',
                boxShadow: isConnected ? '0 0 10px var(--success)' : 'none'
              }}></div>
              <span>{isConnected ? 'System Online' : 'System Offline'}</span>
            </div>
            <button 
              className="btn-toggle inactive" 
              style={{ marginLeft: '16px', padding: '4px 12px', fontSize: '0.9rem' }}
              onClick={() => setCurrentTab('db_viewer')}
            >
              📊 Database Viewer
            </button>
          </div>
        </div>
        {authError && (
          <p style={{ color: 'var(--danger)', marginTop: '12px', fontSize: '0.9rem', maxWidth: '720px' }}>
            {authError}
          </p>
        )}

        <div>
          <button
            className={`btn-toggle ${botEnabled ? 'active' : 'inactive'}`}
            onClick={toggleBot}
          >
            {botEnabled ? 'BOT RUNNING (PAPER)' : 'BOT PAUSED'}
          </button>
          <button
            className="btn-toggle inactive"
            onClick={sendMorningReport}
            disabled={!token}
            style={{ marginLeft: '12px' }}
          >
            📩 Invia Report Telegram
          </button>
          {reportStatus && (
            <p style={{ marginTop: '8px', color: 'var(--text-muted)', fontSize: '0.95rem' }}>
              {reportStatus}
            </p>
          )}
        </div>
      </div>

      <div className="portfolio-dashboard" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '32px' }}>
        <div className="glass-panel stat-card">
          <h3 style={{ color: 'var(--warning)' }}>Simulated Paper Capital</h3>
          <div className="stat-value" style={{ fontSize: '2.5rem', fontWeight: 'bold', color: 'var(--text)' }}>
            ${Number(portfolio.total_capital).toFixed(2)}
          </div>
          <div className="stat-label" style={{ color: 'var(--text-muted)' }}>
            Available USDT: ${Number(portfolio.usdt_balance).toFixed(2)}
          </div>
        </div>

        <div className="glass-panel stat-card">
          <h3>Net Profit / Loss (Paper)</h3>
          <div className="stat-value" style={{
            fontSize: '2.5rem',
            fontWeight: 'bold',
            color: portfolio.net_profit >= 0 ? 'var(--success)' : 'var(--danger)'
          }}>
            {portfolio.net_profit >= 0 ? '+' : ''}${Number(portfolio.net_profit).toFixed(2)}
          </div>
        </div>

        <div className="glass-panel stat-card" style={{ gridColumn: '1 / -1' }}>
          <h3>Active Holdings (Paper)</h3>
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
                    No active paper positions.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="portfolio-dashboard" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '32px' }}>
        <div className="glass-panel stat-card" style={{ gridColumn: '1 / -1', background: 'rgba(20, 25, 40, 0.7)', border: '1px solid var(--primary)' }}>
          <h3 style={{ color: 'var(--primary)' }}>Binance Account (Read-Only)</h3>
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
                    No Binance balances or API keys not configured.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <h2 style={{ marginBottom: '16px' }}>Live Market Analysis</h2>
      <div style={{ display: 'flex', gap: '20px', marginBottom: '32px' }}>
        <div style={{ flex: '1', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px' }}>
          {activeCharts.map(symbol => (
            <div className="glass-panel" key={symbol}>
              <ChartComponent symbol={symbol} tickData={latestTick} />
            </div>
          ))}
          {activeCharts.length === 0 && (
            <div className="glass-panel" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '300px' }}>
              <p style={{ color: 'var(--text-muted)' }}>Select symbols on the right to view charts.</p>
            </div>
          )}
        </div>
        
        <div className="glass-panel" style={{ width: '250px', maxHeight: '500px', overflowY: 'auto' }}>
          <h3 style={{ marginTop: 0, marginBottom: '12px', fontSize: '1rem' }}>Select Charts (Max 3)</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {availableSymbols.map(symbol => {
              const isActive = activeCharts.includes(symbol);
              return (
                <button
                  key={symbol}
                  onClick={() => toggleChart(symbol)}
                  style={{
                    padding: '8px',
                    textAlign: 'left',
                    background: isActive ? 'var(--primary)' : 'rgba(0,0,0,0.3)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    color: 'white',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between'
                  }}
                >
                  <span>{symbol}</span>
                  {isActive && <span>✓</span>}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="glass-panel">
        <h2>Recent Execution Log (Paper)</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th>Price</th>
              <th>Qty</th>
              <th>Strategy</th>
              <th>PnL</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade, i) => {
              const tradeSymbol = trade.symbol || (trade.order && trade.order.symbol);
              const side = trade.side || (trade.order && trade.order.type);
              const price = trade.price || trade.executed_price || trade.exit_price || (trade.order && trade.order.price);
              const quantity = trade.quantity || (trade.order && trade.order.quantity);
              const variant = trade.ab_variant || 'A';

              return (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>
                    {tradeSymbol} <span style={{ fontSize: '0.7rem', color: 'var(--primary)', border: '1px solid var(--primary)', padding: '1px 4px', borderRadius: '4px' }}>{variant}</span>
                  </td>
                  <td>
                    <span className={side === 'BUY' ? 'tag-buy' : 'tag-sell'}>
                      {side}
                    </span>
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: '1.1rem' }}>
                    {price ? `$${Number(price).toFixed(price < 1 ? 4 : 2)}` : '$0.00'}
                  </td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {quantity ? Number(quantity).toFixed(quantity < 1 ? 6 : 2) : 'N/A'}
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>
                    {trade.strategy_name || 'System'}
                  </td>
                  <td style={{ color: trade.pnl_usdt > 0 ? 'var(--success)' : (trade.pnl_usdt < 0 ? 'var(--danger)' : 'var(--text)') }}>
                    {trade.pnl_usdt ? `$${Number(trade.pnl_usdt).toFixed(2)}` : '-'}
                  </td>
                  <td style={{ color: 'var(--text-muted)' }}>{trade.time ? new Date(trade.time).toLocaleTimeString() : '-'}</td>
                </tr>
              );
            })}
            {trades.length === 0 && (
              <tr>
                <td colSpan="7" style={{ textAlign: 'center', padding: '32px', color: 'var(--text)' }}>
                  No paper trades yet. Start the bot and wait for market conditions.
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
