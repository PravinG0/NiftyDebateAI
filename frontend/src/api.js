import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL ? import.meta.env.VITE_API_BASE_URL.replace(/\/+$/, '') : '';

const api = axios.create({
  baseURL: `${API_BASE}/api`,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const getStatus = (symbol = 'NIFTY') =>
  api.get('/status', { params: { symbol } });

export const runDebate = (symbol = 'NIFTY') =>
  api.post('/debate/run', { symbol }, { params: { symbol } });

export const getLatestDebate = (symbol = null) =>
  api.get('/debate/latest', { params: symbol ? { symbol } : {} });

export const getDebateHistory = (limit = 20, offset = 0, symbol = null) =>
  api.get('/debate/history', { params: { limit, offset, ...(symbol ? { symbol } : {}) } });

export const getDebateById = (id) =>
  api.get(`/debate/${id}`);

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

export const startScheduler = () =>
  api.post('/scheduler/start');

export const stopScheduler = () =>
  api.post('/scheduler/stop');

export const getSchedulerStatus = () =>
  api.get('/scheduler/status');

export const getMarketCandles = (symbol = 'NIFTY', interval = '5m', range_period = '5d') =>
  api.get('/market/candles', { params: { symbol, interval, range_period } });

export const getOptionChain = (symbol = 'NIFTY', strikes = 7, expiry = null) =>
  api.get('/market/option-chain', { params: { symbol, strikes, ...(expiry ? { expiry } : {}) } });

export const placeManualTrade = (tradeData) =>
  api.post('/trades/manual', tradeData);

export default api;
