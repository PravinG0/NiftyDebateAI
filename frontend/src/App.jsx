import React, { useState, useEffect, useCallback } from 'react';
import * as api from './api';
import Navbar, { NAV_VIEWS } from './components/Navbar';
import MarketTicker from './components/MarketTicker';
import AuthModal from './components/AuthModal';
import ControlBar from './components/ControlBar';
import TradingViewChart from './components/TradingViewChart';
import DebateArena from './components/DebateArena';
import OptionChainBoard from './components/OptionChainBoard';
import TradeHistory from './components/TradeHistory';
import UserDashboard from './components/UserDashboard';
import ManualTradeModal from './components/ManualTradeModal';
import DebateModal from './components/DebateModal';
import {
  Zap,
  Layers,
  Brain,
  LayoutDashboard,
  Filter,
  Search,
  Eye,
  Clock,
  Sparkles,
} from 'lucide-react';
import { formatIST } from './utils/time';

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
  const [activeView, setActiveView] = useState(NAV_VIEWS.ARENA);
  const [selectedIndex, setSelectedIndex] = useState('NIFTY');

  // Auth Modal State
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState('login');

  // Backend Data State
  const [systemStatus, setSystemStatus] = useState(null);
  const [latestDebate, setLatestDebate] = useState(null);
  const [trades, setTrades] = useState([]);
  const [activeTrades, setActiveTrades] = useState([]);
  const [tradesSummary, setTradesSummary] = useState(null);
  const [debateHistory, setDebateHistory] = useState([]);
  const [schedulerStatus, setSchedulerStatus] = useState(null);

  // Loading & Execution State
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingDebate, setLoadingDebate] = useState(false);
  const [runningDebate, setRunningDebate] = useState(false);
  const [error, setError] = useState(null);

  // Manual Paper Trading Modal State
  const [manualTradeOpen, setManualTradeOpen] = useState(false);
  const [squareOffNotice, setSquareOffNotice] = useState(null);
  const [selectedInspectionDebate, setSelectedInspectionDebate] = useState(null);
  const [debateStudioFilter, setDebateStudioFilter] = useState('ALL');
  const [debateStudioSearch, setDebateStudioSearch] = useState('');

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
      setError('Unable to connect to backend service. Running in offline/cached mode.');
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
      setTrades(Array.isArray(tradesRes.data) ? tradesRes.data : []);
      setActiveTrades(Array.isArray(activeRes.data) ? activeRes.data : []);
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
    await Promise.allSettled([
      fetchStatus(idx),
      fetchSchedulerStatus(),
      fetchLatestDebate(idx),
      fetchTrades(idx),
      fetchDebateHistory(idx),
    ]);
  }, [fetchStatus, fetchSchedulerStatus, fetchLatestDebate, fetchTrades, fetchDebateHistory, selectedIndex]);

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
    }, 12000);
    return () => clearInterval(interval);
  }, [refreshAll, fetchStatus, fetchSchedulerStatus, fetchTrades, selectedIndex]);

  const handleRunDebate = useCallback(async () => {
    setRunningDebate(true);
    setError(null);
    try {
      const res = await api.runDebate(selectedIndex);
      setLatestDebate(res.data);
      await Promise.allSettled([fetchTrades(selectedIndex), fetchDebateHistory(selectedIndex)]);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to execute AI debate for ' + selectedIndex);
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

  // Open Manual Trade Modal with 1-lot guardrails
  const handleOpenManualTrade = useCallback((contract) => {
    const sym = contract?.symbol || selectedIndex;
    const cfg = INDEX_CONFIGS[sym] || INDEX_CONFIGS.NIFTY;
    const isBank = sym === 'BANKNIFTY';
    const spot = isBank
      ? (systemStatus?.banknifty_spot || 52000.0)
      : (systemStatus?.nifty_spot || 25000.0);
    const step = cfg.strikeStep || (isBank ? 100 : 50);
    const defaultStrike = Math.round(spot / step) * step;

    const p = contract?.price || 120.0;
    const lotSize = isBank ? 15 : 25;
    const maxSlPts = Math.floor((700.0 / lotSize) * 100) / 100;
    const maxTgtPts = Math.floor((1000.0 / lotSize) * 100) / 100;
    const defSl = Math.max(1, Math.round((p - maxSlPts) * 10) / 10);
    const defTgt = Math.round((p + maxTgtPts) * 10) / 10;

    if (contract) {
      setTradeModalContract({
        symbol: sym,
        strike: contract.strike || defaultStrike,
        optionType: contract.optionType || 'CE',
        price: p,
        stopLoss: contract.stopLoss || defSl,
        target: contract.target || defTgt,
      });
    } else {
      setTradeModalContract({
        symbol: sym,
        strike: defaultStrike,
        optionType: 'CE',
        price: p,
        stopLoss: defSl,
        target: defTgt,
      });
    }
    setManualTradeOpen(true);
  }, [systemStatus, selectedIndex]);

  const handleTradePlaced = useCallback(async (tradePayload) => {
    await api.placeManualTrade(tradePayload);
    await fetchTrades(selectedIndex);
  }, [fetchTrades, selectedIndex]);

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

  const handleOpenAuth = (mode = 'login') => {
    setAuthModalMode(mode);
    setAuthModalOpen(true);
  };

  const activeSpotPrice = selectedIndex === 'BANKNIFTY'
    ? (systemStatus?.banknifty_spot ?? 52140.80)
    : (systemStatus?.nifty_spot ?? 24986.40);

  // Debate Studio Filtering
  const filteredDebates = debateHistory.filter((d) => {
    const verdict = (
      d.decision ||
      d.verdict ||
      d.judge?.verdict ||
      d.judge_decision?.verdict ||
      ''
    ).toUpperCase();

    if (debateStudioFilter === 'CE' && !verdict.includes('CE') && !verdict.includes('CALL')) return false;
    if (debateStudioFilter === 'PE' && !verdict.includes('PE') && !verdict.includes('PUT')) return false;
    if (debateStudioFilter === 'NO_TRADE' && !verdict.includes('NO_TRADE')) return false;

    if (debateStudioSearch) {
      const q = debateStudioSearch.toLowerCase();
      const sym = (d.index_symbol || d.symbol || '').toLowerCase();
      const reason = (d.judge?.reasoning || d.judge?.rationale || '').toLowerCase();
      if (!sym.includes(q) && !reason.includes(q) && !verdict.toLowerCase().includes(q)) {
        return false;
      }
    }
    return true;
  });

  return (
    <div className="min-h-screen bg-[#080C14] text-slate-100 flex flex-col pb-16 md:pb-6">
      {/* 1. Modern Responsive Navigation Header */}
      <Navbar
        activeView={activeView}
        onSelectView={setActiveView}
        onOpenAuthModal={handleOpenAuth}
        systemStatus={systemStatus}
      />

      {/* 2. Real-Time Multi-Asset Live Ticker Ribbon */}
      <MarketTicker
        systemStatus={systemStatus}
        schedulerStatus={schedulerStatus}
        selectedIndex={selectedIndex}
        onSelectIndex={handleSelectIndex}
      />

      {/* Main Workspace Area */}
      <main className="flex-1 max-w-[1600px] w-full mx-auto px-2 sm:px-4 md:px-6 lg:px-8 py-4 sm:py-6 space-y-4 sm:space-y-6">
        {/* Error Alert Banner */}
        {error && (
          <div className="bg-rose-950/80 border border-rose-800/80 rounded-2xl px-4 py-3 text-rose-200 text-xs sm:text-sm flex items-center justify-between shadow-lg animate-fade-in">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-rose-400 animate-ping flex-shrink-0" />
              <span>{error}</span>
            </div>
            <button
              onClick={() => setError(null)}
              className="ml-4 text-rose-400 hover:text-rose-200 transition-colors p-1"
            >
              ✕
            </button>
          </div>
        )}

        {/* ── VIEW 1: LIVE ARENA (Default) ────────────────────────────────────────── */}
        {activeView === NAV_VIEWS.ARENA && (
          <div className="space-y-4 sm:space-y-6 animate-fade-in">
            {/* Streamlined Control Bar with Angel One Telemetry */}
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

            {/* Live Interactive Chart with Volume Histogram & 20/50 EMAs */}
            <TradingViewChart
              selectedIndex={selectedIndex}
              onSelectIndex={handleSelectIndex}
            />

            {/* Interactive Debate Arena: SentimentMeter, Stepper, Bull/Bear/Judge, Voting */}
            <DebateArena
              debate={latestDebate}
              loading={loadingDebate}
              runningDebate={runningDebate}
              onSelectTrade={handleOpenManualTrade}
              selectedIndex={selectedIndex}
              onPromptAuth={() => handleOpenAuth('login')}
            />

            {/* Active Trades & Global Portfolio Drawer Preview */}
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
          </div>
        )}

        {/* ── VIEW 2: OPTION CHAIN DESK ─────────────────────────────────────────── */}
        {activeView === NAV_VIEWS.OPTION_CHAIN && (
          <div className="space-y-4 animate-fade-in">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg">
              <div>
                <h2 className="text-base sm:text-lg font-bold text-white tracking-tight flex items-center gap-2">
                  <Layers size={18} className="text-blue-400" />
                  <span>{selectedIndex === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'} Option Chain Desk</span>
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Live SmartAPI strikes, Call/Put LTP ladder, and 1-click 1-lot order ticket
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleSelectIndex('NIFTY')}
                  className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${
                    selectedIndex === 'NIFTY'
                      ? 'bg-blue-600 text-white shadow'
                      : 'bg-[#080C14] text-slate-400 hover:text-white border border-slate-800'
                  }`}
                >
                  NIFTY 50
                </button>
                <button
                  onClick={() => handleSelectIndex('BANKNIFTY')}
                  className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${
                    selectedIndex === 'BANKNIFTY'
                      ? 'bg-blue-600 text-white shadow'
                      : 'bg-[#080C14] text-slate-400 hover:text-white border border-slate-800'
                  }`}
                >
                  BANK NIFTY
                </button>
              </div>
            </div>

            <OptionChainBoard
              selectedIndex={selectedIndex}
              spotPrice={activeSpotPrice}
              onSelectTrade={handleOpenManualTrade}
            />
          </div>
        )}

        {/* ── VIEW 3: DEBATE STUDIO (HISTORICAL EXPLORER) ────────────────────────── */}
        {activeView === NAV_VIEWS.STUDIO && (
          <div className="space-y-4 animate-fade-in">
            {/* Filter & Search Bar */}
            <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg space-y-3">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div>
                  <h2 className="text-base sm:text-lg font-bold text-white tracking-tight flex items-center gap-2">
                    <Brain size={18} className="text-blue-400" />
                    <span>Debate Studio Archive</span>
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Search and analyze past multi-agent deliberations, arguments, and jury decisions
                  </p>
                </div>

                {/* Search Input */}
                <div className="relative w-full md:w-64">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    type="text"
                    value={debateStudioSearch}
                    onChange={(e) => setDebateStudioSearch(e.target.value)}
                    placeholder="Search arguments or index..."
                    className="w-full pl-9 pr-3 py-1.5 bg-[#080C14] border border-slate-800 rounded-xl text-xs text-white placeholder-slate-500 outline-none focus:border-blue-500"
                  />
                </div>
              </div>

              {/* Filter Pills */}
              <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-slate-800/80 text-xs">
                <span className="text-slate-500 text-[11px] font-semibold mr-1 flex items-center gap-1">
                  <Filter size={12} /> Verdict:
                </span>
                {['ALL', 'CE', 'PE', 'NO_TRADE'].map((f) => (
                  <button
                    key={f}
                    onClick={() => setDebateStudioFilter(f)}
                    className={`px-2.5 py-1 rounded-lg font-semibold text-[11px] transition-colors ${
                      debateStudioFilter === f
                        ? 'bg-blue-600 text-white shadow'
                        : 'bg-[#080C14] text-slate-400 hover:text-white border border-slate-800'
                    }`}
                  >
                    {f === 'ALL' ? 'All Sessions' : f === 'CE' ? 'Calls (CE)' : f === 'PE' ? 'Puts (PE)' : 'No Trade'}
                  </button>
                ))}
                <span className="ml-auto text-[11px] font-mono text-slate-500">
                  Showing {filteredDebates.length} sessions
                </span>
              </div>
            </div>

            {/* Debate Cards Grid */}
            {filteredDebates.length === 0 ? (
              <div className="bg-[#0D131F] border border-slate-800 rounded-2xl p-12 text-center text-slate-500 text-xs">
                No debate records found matching the current filters.
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
                {filteredDebates.map((item) => {
                  const judge = item.judge || item.judge_verdict || {};
                  const verdict = (judge.verdict || item.decision || 'NO_TRADE').toUpperCase();
                  const isCall = verdict.includes('CE') || verdict.includes('CALL');
                  const isPut = verdict.includes('PE') || verdict.includes('PUT');
                  const bullConf = item.bull?.confidence ?? item.bull_confidence ?? 0;
                  const bearConf = item.bear?.confidence ?? item.bear_confidence ?? 0;

                  return (
                    <div
                      key={item.id}
                      className="p-4 rounded-2xl bg-[#0D131F] border border-slate-800 hover:border-slate-700 transition-all space-y-3 shadow-lg"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-bold text-white">
                            {item.index_symbol || item.symbol || 'NIFTY'}
                          </span>
                          <span className="text-[10px] text-slate-500 font-mono">#{item.id}</span>
                        </div>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase font-mono ${
                          isCall
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            : isPut
                            ? 'bg-rose-950 text-rose-400 border border-rose-800'
                            : 'bg-slate-900 text-amber-400 border border-slate-700'
                        }`}>
                          {verdict}
                        </span>
                      </div>

                      <div className="flex items-center justify-between text-xs font-mono tabular-nums text-slate-400 bg-[#080C14] px-2.5 py-1.5 rounded-xl border border-slate-800/80">
                        <span className="text-emerald-400 font-semibold">🐂 Bull: {bullConf}/10</span>
                        <span className="text-slate-600">•</span>
                        <span className="text-rose-400 font-semibold">🐻 Bear: {bearConf}/10</span>
                      </div>

                      <p className="text-xs text-slate-300 line-clamp-2 italic leading-relaxed">
                        "{judge.reasoning || judge.rationale || 'Adversarial consensus verdict'}"
                      </p>

                      <div className="flex items-center justify-between text-[11px] text-slate-500 font-mono pt-2 border-t border-slate-800/80">
                        <span>{formatIST(item.timestamp_ist || item.created_at)}</span>
                        <button
                          onClick={() => setSelectedInspectionDebate(item)}
                          className="flex items-center gap-1 text-blue-400 hover:text-blue-300 font-semibold"
                        >
                          <Eye size={13} />
                          <span>Inspect</span>
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ── VIEW 4: MY DASHBOARD ──────────────────────────────────────────────── */}
        {activeView === NAV_VIEWS.DASHBOARD && (
          <UserDashboard
            onSelectDebate={(d) => setSelectedInspectionDebate(d)}
            onOpenAuthModal={handleOpenAuth}
            onOpenManualTrade={handleOpenManualTrade}
            onExitTrade={handleExitTrade}
          />
        )}
      </main>

      {/* 3. Mobile Persistent Bottom Navigation Bar (< 768px) */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-[#080C14]/95 backdrop-blur-lg border-t border-slate-800 px-3 py-2 flex items-center justify-around">
        <button
          onClick={() => setActiveView(NAV_VIEWS.ARENA)}
          className={`flex flex-col items-center gap-1 text-[10px] font-semibold transition-colors ${
            activeView === NAV_VIEWS.ARENA ? 'text-blue-400' : 'text-slate-400'
          }`}
        >
          <Zap size={18} />
          <span>Arena</span>
        </button>

        <button
          onClick={() => setActiveView(NAV_VIEWS.OPTION_CHAIN)}
          className={`flex flex-col items-center gap-1 text-[10px] font-semibold transition-colors ${
            activeView === NAV_VIEWS.OPTION_CHAIN ? 'text-blue-400' : 'text-slate-400'
          }`}
        >
          <Layers size={18} />
          <span>Chain</span>
        </button>

        <button
          onClick={() => setActiveView(NAV_VIEWS.STUDIO)}
          className={`flex flex-col items-center gap-1 text-[10px] font-semibold transition-colors ${
            activeView === NAV_VIEWS.STUDIO ? 'text-blue-400' : 'text-slate-400'
          }`}
        >
          <Brain size={18} />
          <span>Studio</span>
        </button>

        <button
          onClick={() => setActiveView(NAV_VIEWS.DASHBOARD)}
          className={`flex flex-col items-center gap-1 text-[10px] font-semibold transition-colors ${
            activeView === NAV_VIEWS.DASHBOARD ? 'text-blue-400' : 'text-slate-400'
          }`}
        >
          <LayoutDashboard size={18} />
          <span>Dashboard</span>
        </button>
      </div>

      {/* 4. Modals */}
      {/* Authentication Dialog (Sign In / Register / Guest) */}
      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        initialMode={authModalMode}
      />

      {/* Guardrail-Enforced Manual Paper Trade Modal */}
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

      {/* Debate Detail Inspection Modal */}
      {selectedInspectionDebate && (
        <DebateModal
          debate={selectedInspectionDebate}
          onClose={() => setSelectedInspectionDebate(null)}
        />
      )}
    </div>
  );
}
