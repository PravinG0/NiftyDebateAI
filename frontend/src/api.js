import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL
  ? import.meta.env.VITE_API_BASE_URL.replace(/\/+$/, '')
  : '';

export const TOKEN_STORAGE_KEY = 'niftydebate_token';
export const USER_STORAGE_KEY = 'niftydebate_user';

const api = axios.create({
  baseURL: `${API_BASE}/api`,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request Interceptor: Attach JWT Bearer Token if available
api.interceptors.request.use(
  (config) => {
    try {
      const token = localStorage.getItem(TOKEN_STORAGE_KEY);
      if (token && !config.headers.Authorization) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    } catch {
      // localStorage may fail in restricted/private contexts
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response Interceptor: Gracefully handle 401 Unauthorized
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      try {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        localStorage.removeItem(USER_STORAGE_KEY);
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('niftydebate_auth_expired'));
        }
      } catch {
        // ignore
      }
    }
    return Promise.reject(error);
  }
);

// ── Auth API Subsystem ──────────────────────────────────────────────────────────
export const auth = {
  register: (userData) => api.post('/auth/register', userData),
  login: (credentials) => api.post('/auth/login', {
    username_or_email: credentials.username || credentials.username_or_email || credentials.email,
    password: credentials.password,
  }),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout').catch(() => ({ data: { status: 'logged_out' } })),
};

// ── User-Scoped API Subsystem ──────────────────────────────────────────────────
export const user = {
  getTrades: (params = {}) => api.get('/user/trades', { params }),
  getSummary: () => api.get('/user/trades/summary'),
  getWatchlist: () => api.get('/user/watchlist'),
  addToWatchlist: (item) => api.post('/user/watchlist', item),
  removeFromWatchlist: (id) => api.delete(`/user/watchlist/${id}`),
  voteDebate: (debateId, voteData) => api.post(`/debates/${debateId}/vote`, voteData),
  getDebateVotes: (debateId) => api.get(`/debates/${debateId}/votes`),
};

// ── System & Market Endpoints ──────────────────────────────────────────────────
export const getStatus = (symbol = 'NIFTY') =>
  api.get('/status', { params: { symbol } });

export const getMarketQuotes = (symbols = 'NIFTY,BANKNIFTY') =>
  api.get('/market/quotes', { params: { symbols } });

export const getMarketCandles = (symbol = 'NIFTY', interval = '5m', range_period = '5d') =>
  api.get('/market/candles', { params: { symbol, interval, range_period } });

export const getOptionChain = (symbol = 'NIFTY', strikes = 7, expiry = null) =>
  api.get('/market/option-chain', { params: { symbol, strikes, ...(expiry ? { expiry } : {}) } });

// ── Debate Endpoints ───────────────────────────────────────────────────────────
export const runDebate = (symbol = 'NIFTY') =>
  api.post('/debate/run', { symbol }, { params: { symbol } });

export const getLatestDebate = (symbol = null) =>
  api.get('/debate/latest', { params: symbol ? { symbol } : {} });

export const getDebateHistory = (limit = 20, offset = 0, symbol = null) =>
  api.get('/debate/history', { params: { limit, offset, ...(symbol ? { symbol } : {}) } });

export const getDebateById = (id) =>
  api.get(`/debate/${id}`);

export const voteDebate = (debateId, voteData) =>
  user.voteDebate(debateId, voteData);

export const getDebateVotes = (debateId) =>
  user.getDebateVotes(debateId);

// ── Trade Endpoints ────────────────────────────────────────────────────────────
export const getTrades = (symbol = null) =>
  api.get('/trades', { params: symbol ? { symbol } : {} });

export const getActiveTrades = (symbol = null) =>
  api.get('/trades/active', { params: symbol ? { symbol } : {} });

export const getTradesSummary = (symbol = null) =>
  api.get('/trades/summary', { params: symbol ? { symbol } : {} });

export const closeTrade = (id, exitPrice) =>
  api.patch(`/trades/${id}/close`, { exit_price: exitPrice });

export const exitTrade = (id, exitPrice = null) =>
  api.patch(`/trades/${id}/close`, { exit_price: exitPrice });

export const placeManualTrade = (tradeData) =>
  api.post('/trades/manual', tradeData);

// ── Scheduler Endpoints ────────────────────────────────────────────────────────
export const startScheduler = () =>
  api.post('/scheduler/start');

export const stopScheduler = () =>
  api.post('/scheduler/stop');

export const getSchedulerStatus = () =>
  api.get('/scheduler/status');

// Attach helper namespace to default export
api.auth = auth;
api.user = user;

export default api;
