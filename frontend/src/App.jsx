import React, { useState, useEffect, useCallback } from 'react';
import * as api from './api';
import ControlBar from './components/ControlBar';
import TradingViewChart from './components/TradingViewChart';
import DebateArena from './components/DebateArena';
import TradeHistory from './components/TradeHistory';
import ManualTradeModal from './components/ManualTradeModal';

export const INDEX_CONFIGS = {
  NIFTY: {
    symbol: 'NIFTY',
    displayName: 'NIFTY 50',
    chartSymbol: '^NSEI',
    tvSymbol: 'NSE:NIFTY',
    lotSize: 25,
    strikeStep: 50,
    budgetLimit: 5000,
  },
  BANKNIFTY: {
    symbol: 'BANKNIFTY',
    displayName: 'BANK NIFTY',
    chartSymbol: '^NSEBANK',
    tvSymbol: 'NSE:BANKNIFTY',
    lotSize: 15,
    strikeStep: 100,
    budgetLimit: 5000,
  },
};

export default function App() {
  const [selectedIndex, setSelectedIndex] = useState('NIFTY');
  const [systemStatus, setSystemStatus] = useState(null);
  const [latestDebate, setLatestDebate] = useState(null);
  const [trades, setTrades] = useState([]);
  const [activeTrades, setActiveTrades] = useState([]);
  const [tradesSummary, setTradesSummary] = useState(null);
  const [debateHistory, setDebateHistory] = useState([]);
  const [schedulerStatus, setSchedulerStatus] = useState(null);

  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingDebate, setLoadingDebate] = useState(false);
  const [runningDebate, setRunningDebate] = useState(false);
  const [error, setError] = useState(null);

  // Manual Paper Trading Modal State
  const [manualTradeOpen, setManualTradeOpen] = useState(false);
  const [squareOffNotice, setSquareOffNotice] = useState(null);
  const [tradeModalContract, setTradeModalContract] = useState({
    symbol: 'NIFTY',
    strike: 25000,
    optionType: 'CE',
    price: 120.0,
    stopLoss: 90.0,
    target: 160.0,
  });

  const fetchStatus = useCallback(async (idx = selectedIndex) => {
    try {
      const res = await api.getStatus(idx);
      setSystemStatus(res.data);
      setError(null);
    } catch (err) {
      setError('Failed to connect to backend server');
    } finally {
      setLoadingStatus(false);
    }
  }, [selectedIndex]);

  const fetchSchedulerStatus = useCallback(async () => {
    try {
      const res = await api.getSchedulerStatus();
      setSchedulerStatus(res.data);
    } catch {
      /* ignore */
    }
  }, []);

  const fetchLatestDebate = useCallback(async (idx = selectedIndex) => {
    try {
      setLoadingDebate(true);
      const res = await api.getLatestDebate(idx);
      setLatestDebate(res.data);
    } catch {
      /* no debate yet */
    } finally {
      setLoadingDebate(false);
    }
  }, [selectedIndex]);

  const fetchTrades = useCallback(async (idx = selectedIndex) => {
    try {
      const [tradesRes, activeRes, summaryRes] = await Promise.all([
        api.getTrades(idx),
        api.getActiveTrades(idx),
        api.getTradesSummary(idx),
      ]);
      setTrades(tradesRes.data);
      setActiveTrades(activeRes.data);
      setTradesSummary(summaryRes.data);
    } catch {
      /* ignore */
    }
  }, [selectedIndex]);

  const fetchDebateHistory = useCallback(async (idx = selectedIndex) => {
    try {
      const res = await api.getDebateHistory(50, 0, idx);
      setDebateHistory(Array.isArray(res.data) ? res.data : res.data.debates || []);
    } catch {
      /* ignore */
    }
  }, [selectedIndex]);

  const refreshAll = useCallback(async (idx = selectedIndex) => {
    await Promise.all([
      fetchStatus(idx),
      fetchSchedulerStatus(),
      fetchLatestDebate(idx),
      fetchTrades(idx),
      fetchDebateHistory(idx),
    ]);
  }, [fetchStatus, fetchSchedulerStatus, fetchLatestDebate, fetchTrades, fetchDebateHistory, selectedIndex]);

  // Handle switching between NIFTY 50 and BANK NIFTY
  const handleSelectIndex = useCallback((newIndex) => {
    setSelectedIndex(newIndex);
    refreshAll(newIndex);
  }, [refreshAll]);

  useEffect(() => {
    refreshAll(selectedIndex);
    const interval = setInterval(() => {
      fetchStatus(selectedIndex);
      fetchSchedulerStatus();
      fetchTrades(selectedIndex);
    }, 10000);
    return () => clearInterval(interval);
  }, [refreshAll, fetchStatus, fetchSchedulerStatus, fetchTrades, selectedIndex]);

  const handleRunDebate = useCallback(async () => {
    setRunningDebate(true);
    setError(null);
    try {
      const res = await api.runDebate(selectedIndex);
      setLatestDebate(res.data);
      await Promise.all([fetchTrades(selectedIndex), fetchDebateHistory(selectedIndex)]);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to run debate for ' + selectedIndex);
    } finally {
      setRunningDebate(false);
    }
  }, [fetchTrades, fetchDebateHistory, selectedIndex]);

  const handleStartScheduler = useCallback(async () => {
    try {
      await api.startScheduler();
      await fetchSchedulerStatus();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to start scheduler');
    }
  }, [fetchSchedulerStatus]);

  const handleStopScheduler = useCallback(async () => {
    try {
      await api.stopScheduler();
      await fetchSchedulerStatus();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to stop scheduler');
    }
  }, [fetchSchedulerStatus]);

  // Handle opening Manual Trade Modal with 1-lot and budget guardrail
  const handleOpenManualTrade = useCallback((contract) => {
    const sym = contract?.symbol || selectedIndex;
    const cfg = INDEX_CONFIGS[sym] || INDEX_CONFIGS.NIFTY;
    const isBank = sym === 'BANKNIFTY';
    const spot = isBank
      ? (systemStatus?.banknifty_spot || 52000.0)
      : (systemStatus?.nifty_spot || 25000.0);
    const step = cfg.strikeStep || (isBank ? 100 : 50);
    const defaultStrike = Math.round(spot / step) * step;

    if (contract) {
      setTradeModalContract({
        symbol: sym,
        strike: contract.strike || defaultStrike,
        optionType: contract.optionType || 'CE',
        price: contract.price || 120.0,
        stopLoss: contract.stopLoss || Math.round((contract.price || 120.0) * 0.75),
        target: contract.target || Math.round((contract.price || 120.0) * 1.50),
      });
    } else {
      setTradeModalContract({
        symbol: sym,
        strike: defaultStrike,
        optionType: 'CE',
        price: 120.0,
        stopLoss: 90.0,
        target: 160.0,
      });
    }
    setManualTradeOpen(true);
  }, [systemStatus, selectedIndex]);

  // Handle placing a manual trade
  const handleTradePlaced = useCallback(async (tradePayload) => {
    await api.placeManualTrade(tradePayload);
    await fetchTrades(selectedIndex);
  }, [fetchTrades, selectedIndex]);

  // Handle exiting / squaring off a trade
  const handleExitTrade = useCallback(async (tradeId) => {
    try {
      const res = await api.exitTrade(tradeId);
      setSquareOffNotice(res.data);
      await fetchTrades(selectedIndex);
      return res.data;
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to square off trade');
    }
  }, [fetchTrades, selectedIndex]);

  const activeSpotPrice = selectedIndex === 'BANKNIFTY'
    ? (systemStatus?.banknifty_spot ?? 52140.80)
    : (systemStatus?.nifty_spot ?? 24986.40);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="relative z-10 max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-5 space-y-6">
        {/* Error Banner */}
        {error && (
          <div className="bg-rose-950 border border-rose-800 rounded-xl px-4 py-3 text-rose-200 text-sm flex items-center justify-between shadow-md">
            <span>{error}</span>
            <button
              onClick={() => setError(null)}
              className="ml-4 text-rose-400 hover:text-rose-200 transition-colors"
            >
              ✕
            </button>
          </div>
        )}

        {/* Control Bar */}
        <ControlBar
          systemStatus={systemStatus}
          schedulerStatus={schedulerStatus}
          selectedIndex={selectedIndex}
          onSelectIndex={handleSelectIndex}
          runningDebate={runningDebate}
          loadingStatus={loadingStatus}
          onRunDebate={handleRunDebate}
          onStartScheduler={handleStartScheduler}
          onStopScheduler={handleStopScheduler}
          onOpenManualTrade={handleOpenManualTrade}
        />

        {/* Live Interactive Chart */}
        <TradingViewChart
          selectedIndex={selectedIndex}
          onSelectIndex={handleSelectIndex}
        />

        {/* Debate Arena */}
        <DebateArena
          debate={latestDebate}
          loading={loadingDebate}
          onSelectTrade={handleOpenManualTrade}
          selectedIndex={selectedIndex}
        />

        {/* Trade History & Option Chain */}
        <TradeHistory
          selectedIndex={selectedIndex}
          trades={trades}
          activeTrades={activeTrades}
          tradesSummary={tradesSummary}
          debateHistory={debateHistory}
          onOpenManualTrade={handleOpenManualTrade}
          onExitTrade={handleExitTrade}
          spotPrice={activeSpotPrice}
          squareOffNotice={squareOffNotice}
          onDismissNotice={() => setSquareOffNotice(null)}
        />

        {/* Manual Paper Trade Modal */}
        <ManualTradeModal
          isOpen={manualTradeOpen}
          onClose={() => setManualTradeOpen(false)}
          selectedIndex={tradeModalContract.symbol || selectedIndex}
          initialStrike={tradeModalContract.strike}
          initialType={tradeModalContract.optionType}
          initialPrice={tradeModalContract.price}
          spotPrice={activeSpotPrice}
          onTradePlaced={handleTradePlaced}
        />
      </div>
    </div>
  );
}
