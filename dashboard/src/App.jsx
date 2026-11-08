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
  const [closedTrades, setClosedTrades] = useState([]);
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
  const [realPortfolioError, setRealPortfolioError] = useState(null);

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

  const fetchClosedTrades = (jwt) => {
    fetch(apiUrl('/api/trades/closed/today?limit=15'), {
      headers: { 'Authorization': `Bearer ${jwt}` }
    })
      .then((res) => res.json())
      .then((data) => {
        if (data && data.trades) {
          setClosedTrades(data.trades);
        }
      })
      .catch((err) => console.error("Failed to fetch closed trades:", err));
  };

  const fetchRealPortfolio = (jwt) => {
    fetch(apiUrl('/api/portfolio/real'), {
      headers: { 'Authorization': `Bearer ${jwt}` }
    })
      .then((res) => res.json())
      .then((data) => {
        if (data && data.status === 'ok') {
          setRealPortfolio(data);
          setRealPortfolioError(null);
        } else if (data && data.status === 'error') {
          setRealPortfolioError(data.message || 'Errore API Crypto.com');
        }
      })
      .catch((err) => {
        console.error("Failed to fetch real portfolio:", err);
        setRealPortfolioError(err.message || "Errore di connessione API Gateway");
      });
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
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((loginData) => {
        if (loginData && loginData.access_token) {
          setToken(loginData.access_token);
          setAuthError(null);
          fetchBotStatus(loginData.access_token);
          fetchTrades(loginData.access_token);
          fetchRealPortfolio(loginData.access_token);
          fetchSymbols(loginData.access_token);
          fetchClosedTrades(loginData.access_token);

          const ws = new WebSocket(wsUrl(`/ws/live?token=${loginData.access_token}`));

          ws.onopen = () => {
            setIsConnected(true);
            console.log('Connected to API Gateway WebSocket');
          };

          ws.onmessage = (event) => {
            try {
              const msg = JSON.parse(event.data);
              if (msg.channel && msg.channel.startsWith('ticks:')) {
                setLatestTick(msg.data);
              } else if (msg.channel === 'executed_trades') {
                setTrades(prev => [msg.data, ...prev].slice(0, 50));
                // Refresh closed trades from API when a new trade is executed
                if (loginData.access_token) {
                  fetchClosedTrades(loginData.access_token);
                }
              } else if (msg.channel === 'system' && msg.data && msg.data.bot_status !== undefined) {
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
          'Login fallito: la password nel build della dashboard non coincide con ADMIN_PASSWORD nel .env.'
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

      {/* ─── KPI Cards ─── */}
      <div className="kpi-grid">
        {/* Card 1: Capitale Paper */}
        <div className={`glass-panel kpi-card kpi-card--capital`}>
          <div className="kpi-label">Paper Capital</div>
          <div className="kpi-value">${Number(portfolio.total_capital).toFixed(2)}</div>
          <div className="kpi-sub">
            Iniziale: <span>${Number(portfolio.starting_capital || 200).toFixed(0)}</span>
            {' · '}
            <span style={{ color: portfolio.net_profit_pct >= 0 ? 'var(--success)' : 'var(--danger)' }}>
              {portfolio.net_profit_pct >= 0 ? '▲' : '▼'} {Math.abs(portfolio.net_profit_pct || 0).toFixed(2)}%
            </span>
          </div>
        </div>

        {/* Card 2: PnL Oggi */}
        <div className={`glass-panel kpi-card ${(portfolio.pnl_today || portfolio.net_profit) >= 0 ? 'kpi-card--pnl-pos' : 'kpi-card--pnl-neg'}`}>
          <div className="kpi-label">PnL Oggi</div>
          <div className="kpi-value" style={{
            color: (portfolio.pnl_today || portfolio.net_profit) >= 0 ? 'var(--success)' : 'var(--danger)'
          }}>
            {(portfolio.pnl_today || portfolio.net_profit) >= 0 ? '+' : ''}
            ${Number(portfolio.pnl_today || portfolio.net_profit).toFixed(2)}
          </div>
          <div className="kpi-sub">
            Best: <span style={{ color: 'var(--success)' }}>${Number(portfolio.best_trade || 0).toFixed(2)}</span>
            {' · Worst: '}
            <span style={{ color: 'var(--danger)' }}>${Number(portfolio.worst_trade || 0).toFixed(2)}</span>
          </div>
        </div>

        {/* Card 3: Trade Oggi */}
        <div className="glass-panel kpi-card kpi-card--trades">
          <div className="kpi-label">Trade Oggi</div>
          <div className="kpi-value kpi-value--sm">
            {portfolio.trades_today || 0}
          </div>
          <div className="kpi-sub">
            Win Rate: <span style={{ color: (portfolio.win_rate_today || 0) >= 50 ? 'var(--success)' : 'var(--danger)' }}>
              {Number(portfolio.win_rate_today || 0).toFixed(1)}%
            </span>
            {' · '}
            <span style={{ color: 'var(--success)' }}>W{portfolio.wins_today || 0}</span>
            {' / '}
            <span style={{ color: 'var(--danger)' }}>L{portfolio.losses_today || 0}</span>
          </div>
        </div>

        {/* Card 4: Stato Risk Manager */}
        <div className={`glass-panel kpi-card ${portfolio.circuit_breaker_status === 'open' ? 'kpi-card--risk-alert' : 'kpi-card--risk-ok'}`}>
          <div className="kpi-label">Risk Manager</div>
          <div style={{ marginTop: '4px' }}>
            <span className={`status-badge ${portfolio.circuit_breaker_status === 'open' ? 'status-badge--alert' : 'status-badge--ok'}`}>
              {portfolio.circuit_breaker_status === 'open' ? '⚠ CB OPEN' : '✅ CB Closed'}
            </span>
          </div>
          {portfolio.circuit_breaker_status === 'open' && portfolio.circuit_breaker_reason && (
            <div className="kpi-sub" style={{ color: 'var(--danger)' }}>
              {portfolio.circuit_breaker_reason}
            </div>
          )}
          <div className="kpi-sub">
            Ordini pendenti: <span>{portfolio.pending_orders || 0}</span>
          </div>
        </div>
      </div>

      {/* ─── Ultimi Trade Chiusi (Oggi) ─── */}
      <div className="glass-panel" style={{ marginBottom: '24px' }}>
        <div className="section-title">
          <h3>📊 Ultimi Trade Chiusi Oggi</h3>
          <span className="count-badge">{closedTrades.length} trade</span>
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Ora</th>
              <th>Simbolo</th>
              <th>Side</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>PnL ($)</th>
              <th>PnL %</th>
              <th>Strategia</th>
              <th>Motivo</th>
            </tr>
          </thead>
          <tbody>
            {closedTrades.map((t, i) => {
              const pnl = Number(t.pnl_usdt || 0);
              const pnlPct = Number(t.pnl_pct || 0);
              const pnlClass = pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : 'pnl-zero';
              const reason = (t.close_reason || '').toUpperCase();
              let reasonClass = 'close-reason-tag--other';
              if (reason.includes('SL') || reason.includes('STOP')) reasonClass = 'close-reason-tag--sl';
              else if (reason.includes('TP') || reason.includes('TAKE')) reasonClass = 'close-reason-tag--tp';
              else if (reason.includes('TRAIL')) reasonClass = 'close-reason-tag--trailing';
              
              return (
                <tr key={i}>
                  <td>{t.close_time ? new Date(t.close_time_ms || t.close_time).toLocaleTimeString() : '—'}</td>
                  <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                  <td style={{ color: t.side === 'BUY' ? 'var(--success)' : 'var(--danger)' }}>{t.side}</td>
                  <td>${Number(t.entry_price || 0).toPrecision(6)}</td>
                  <td>${Number(t.exit_price || 0).toPrecision(6)}</td>
                  <td className={pnlClass}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}</td>
                  <td className={pnlClass}>{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</td>
                  <td><span className="strategy-tag">{t.strategy_name || '—'}</span></td>
                  <td><span className={`close-reason-tag ${reasonClass}`}>{t.close_reason || '—'}</span></td>
                </tr>
              );
            })}
            {closedTrades.length === 0 && (
              <tr>
                <td colSpan="9" style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)' }}>
                  Nessun trade chiuso oggi.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ─── Active Holdings (Paper) — Originale ─── */}
      <div className="glass-panel" style={{ marginBottom: '24px' }}>
        <div className="section-title">
          <h3>📈 Active Holdings (Paper)</h3>
        </div>
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

      <div className="portfolio-dashboard" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '32px' }}>
        <div className="glass-panel stat-card" style={{ gridColumn: '1 / -1', background: 'rgba(20, 25, 40, 0.7)', border: '1px solid var(--primary)' }}>
          <h3 style={{ color: 'var(--primary)' }}>Crypto.com Account (Read-Only)</h3>
          {realPortfolioError ? (
            <div style={{ color: 'var(--danger)', padding: '12px 16px', background: 'rgba(255, 75, 75, 0.1)', borderRadius: '8px', marginBottom: '16px', fontSize: '0.95rem' }}>
              ⚠️ Errore API Crypto.com: {realPortfolioError}
            </div>
          ) : (
            <div style={{ marginBottom: '16px' }}>
              <span style={{ fontSize: '1rem', color: 'var(--text-muted)' }}>Total Value: </span>
              <span style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--text)' }}>
                ${Number(realPortfolio.total_value_usdt).toFixed(2)}
              </span>
            </div>
          )}
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
                    {realPortfolioError ? "Impossibile recuperare i saldi reali." : "Nessun saldo disponibile sul conto Crypto.com."}
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
              <th>Time</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Price</th>
              <th>Quantity</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={i}>
                <td>{new Date(t.timestamp || Date.now()).toLocaleTimeString()}</td>
                <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                <td style={{ color: t.type === 'BUY' ? 'var(--success)' : 'var(--danger)' }}>{t.type}</td>
                <td>${Number(t.price).toFixed(2)}</td>
                <td>{Number(t.quantity).toFixed(4)}</td>
                <td>
                  <span className={`mode-badge ${t.status === 'FILLED' ? 'mode-live' : 'mode-paper'}`}>
                    {t.status}
                  </span>
                </td>
              </tr>
            ))}
            {trades.length === 0 && (
              <tr>
                <td colSpan="6" style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)' }}>
                  No execution logs yet.
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
