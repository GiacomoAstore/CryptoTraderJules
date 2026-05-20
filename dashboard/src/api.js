const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

export function apiUrl(path) {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${normalized}`;
}

export function wsUrl(path) {
  const wsBase = (import.meta.env.VITE_WS_URL || API_BASE.replace(/^http/, 'ws')).replace(/\/$/, '');
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${wsBase}${normalized}`;
}

export { API_BASE };
