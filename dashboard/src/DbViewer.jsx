import React, { useState, useEffect } from 'react';
import { apiUrl } from './api';
import './App.css';

function DbViewer({ token, onBack }) {
  const [tables, setTables] = useState([]);
  const [selectedTable, setSelectedTable] = useState('');
  const [data, setData] = useState([]);
  const [columns, setColumns] = useState([]);
  const [filters, setFilters] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Fetch tables on mount
    fetch(apiUrl('/api/db/tables'), {
      headers: { 'Authorization': `Bearer ${token}` }
    })
      .then(res => res.json())
      .then(res => {
        if (res.status === 'ok') {
          setTables(res.tables);
          if (res.tables.length > 0) {
            setSelectedTable(res.tables[0]);
          }
        } else {
          setError(res.message);
        }
      })
      .catch(err => setError(err.toString()));
  }, [token]);

  useEffect(() => {
    if (selectedTable) {
      fetchData();
    }
  }, [selectedTable]);

  const fetchData = () => {
    setLoading(true);
    setError(null);
    
    // Clean empty filters
    const activeFilters = {};
    Object.keys(filters).forEach(k => {
      if (filters[k]) activeFilters[k] = filters[k];
    });

    fetch(apiUrl(`/api/db/table/${selectedTable}`), {
      method: 'POST',
      headers: { 
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ limit: 1000, filters: activeFilters })
    })
      .then(res => res.json())
      .then(res => {
        setLoading(false);
        if (res.status === 'ok') {
          setData(res.data);
          if (res.data.length > 0) {
            setColumns(Object.keys(res.data[0]));
          } else {
            setColumns([]);
          }
        } else {
          setError(res.message);
        }
      })
      .catch(err => {
        setLoading(false);
        setError(err.toString());
      });
  };

  const handleFilterChange = (col, value) => {
    setFilters(prev => ({ ...prev, [col]: value }));
  };

  const applyFilters = (e) => {
    if (e.key === 'Enter') {
      fetchData();
    }
  };

  return (
    <div className="dashboard-container">
      <div className="header glass-panel">
        <div>
          <h1>Database Viewer</h1>
          <p style={{ color: 'var(--text-muted)' }}>Explore raw database records</p>
        </div>
        <div>
          <button className="btn-toggle active" onClick={onBack}>
            ← Torna al Bot
          </button>
        </div>
      </div>

      <div className="glass-panel" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <label style={{ fontWeight: 'bold' }}>Select Table:</label>
          <select 
            value={selectedTable} 
            onChange={(e) => setSelectedTable(e.target.value)}
            style={{ padding: '8px', borderRadius: '4px', background: 'rgba(0,0,0,0.2)', color: 'white', border: '1px solid var(--primary)' }}
          >
            {tables.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <button 
            onClick={fetchData} 
            style={{ padding: '8px 16px', background: 'var(--primary)', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
          >
            Refresh
          </button>
          {loading && <span style={{ color: 'var(--primary)' }}>Loading...</span>}
        </div>
        {error && <div style={{ color: 'var(--danger)', marginTop: '10px' }}>{error}</div>}
      </div>

      <div className="glass-panel" style={{ overflowX: 'auto' }}>
        <table className="data-table" style={{ width: '100%', minWidth: '800px' }}>
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col}>
                  <div style={{ marginBottom: '8px' }}>{col}</div>
                  <input 
                    type="text" 
                    placeholder={`Filter ${col}...`}
                    value={filters[col] || ''}
                    onChange={(e) => handleFilterChange(col, e.target.value)}
                    onKeyDown={applyFilters}
                    style={{ width: '100%', padding: '4px', fontSize: '0.8rem', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.2)', color: 'white' }}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i}>
                {columns.map(col => (
                  <td key={col} style={{ whiteSpace: 'nowrap', maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {row[col] !== null ? String(row[col]) : <span style={{ color: 'var(--text-muted)' }}>NULL</span>}
                  </td>
                ))}
              </tr>
            ))}
            {data.length === 0 && !loading && (
              <tr>
                <td colSpan={columns.length || 1} style={{ textAlign: 'center', padding: '32px' }}>
                  No records found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DbViewer;
