import React, { useState, useEffect, useCallback } from 'react';
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  Activity,
  Award,
  Bookmark,
  CheckCircle2,
  Clock,
  Layers,
  ArrowUpRight,
  LogOut,
  User,
  ShieldCheck,
  RefreshCw,
  ExternalLink,
  ChevronRight,
  Sparkles,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { user as userApi, getTradesSummary, getTrades } from '../api';
import { formatIST } from '../utils/time';
import { SAVED_DEBATES_KEY } from './DebateArena';

export default function UserDashboard({
  onSelectDebate,
  onOpenAuthModal,
  onOpenManualTrade,
  onExitTrade,
}) {
  const { user, isAuthenticated, isGuest } = useAuth();
  const [summary, setSummary] = useState(null);
  const [trades, setTrades] = useState([]);
  const [savedDebates, setSavedDebates] = useState([]);
  const [activeTab, setActiveTab] = useState('portfolio'); // 'portfolio' | 'saved' | 'votes'
  const [loading, setLoading] = useState(true);

  // Load user data
  const loadUserData = useCallback(async () => {
    setLoading(true);
    try {
      if (isAuthenticated) {
        // Authenticated user API calls
        const [sumRes, trRes] = await Promise.allSettled([
          userApi.getSummary(),
          userApi.getTrades(),
        ]);

        if (sumRes.status === 'fulfilled' && sumRes.value.data) {
          setSummary(sumRes.value.data);
        }
        if (trRes.status === 'fulfilled' && trRes.value.data) {
          setTrades(Array.isArray(trRes.value.data) ? trRes.value.data : []);
        }
      } else {
        // Guest mode fallback: fetch global/local paper trades summary
        const [sumRes, trRes] = await Promise.allSettled([
          getTradesSummary(),
          getTrades(),
        ]);
        if (sumRes.status === 'fulfilled' && sumRes.value.data) {
          setSummary(sumRes.value.data);
        }
        if (trRes.status === 'fulfilled' && trRes.value.data) {
          setTrades(Array.isArray(trRes.value.data) ? trRes.value.data : []);
        }
      }
    } catch (err) {
      console.error('Failed to load user portfolio data:', err);
    } finally {
      setLoading(false);
    }

    // Load saved debates from localStorage
    try {
      const saved = JSON.parse(localStorage.getItem(SAVED_DEBATES_KEY) || '[]');
      setSavedDebates(saved);
    } catch {
      setSavedDebates([]);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    loadUserData();
  }, [loadUserData]);

  // Derived metrics with fallback
  const totalTrades = summary?.total_trades ?? trades.length;
  const winCount = summary?.win_trades ?? summary?.wins ?? 0;
  const lossCount = summary?.loss_trades ?? summary?.losses ?? 0;
  const winRate = summary?.win_rate != null ? Number(summary.win_rate).toFixed(1) : (totalTrades > 0 ? ((winCount / totalTrades) * 100).toFixed(1) : '0.0');
  const netPnl = summary?.net_pnl ?? summary?.total_pnl ?? 0;
  const profitFactor = summary?.profit_factor ? Number(summary.profit_factor).toFixed(2) : '1.85';
  const openPositions = trades.filter((t) => t.status === 'OPEN' || t.exit_price == null);

  const virtualMargin = 100000; // ₹1,00,000 allocated starting margin
  const currentMargin = virtualMargin + Number(netPnl);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header Banner */}
      <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-5 sm:p-6 shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-3.5">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center text-white font-extrabold text-lg shadow-lg shadow-blue-500/20">
            {isAuthenticated ? user.username?.charAt(0).toUpperCase() : 'G'}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg sm:text-xl font-bold text-white tracking-tight">
                {isAuthenticated ? `${user.full_name || user.username}'s Dashboard` : 'Guest Trader Dashboard'}
              </h2>
              <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full border ${
                isAuthenticated
                  ? 'bg-emerald-950/80 border-emerald-700/60 text-emerald-300'
                  : 'bg-slate-900 border-slate-700 text-slate-400'
              }`}>
                {isAuthenticated ? 'Cloud Synced' : 'Guest Mode'}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              {isAuthenticated
                ? user.email
                : 'Local session paper portfolio • 1-Lot risk enforced'}
            </p>
          </div>
        </div>

        {/* Guest Warning / Sign In Callout */}
        {isGuest && (
          <div className="flex items-center gap-3 bg-blue-950/40 border border-blue-600/40 rounded-xl px-4 py-2.5">
            <div className="text-xs text-blue-200">
              <span className="font-bold">Save your trading journey:</span> Sign in to persist stats and sync across devices.
            </div>
            <button
              onClick={() => onOpenAuthModal && onOpenAuthModal('register')}
              className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs whitespace-nowrap shadow transition-colors"
            >
              Sign Up Free
            </button>
          </div>
        )}

        <button
          onClick={loadUserData}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 hover:text-white text-xs font-semibold transition-colors"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin text-blue-400' : ''} />
          <span>Refresh</span>
        </button>
      </div>

      {/* 4 Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {/* Margin Balance */}
        <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="font-semibold">Paper Margin</span>
            <Wallet size={15} className="text-blue-400" />
          </div>
          <div className="text-lg sm:text-2xl font-bold font-mono text-white tabular-nums">
            ₹{currentMargin.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
          </div>
          <div className="text-[10px] text-slate-500 font-mono">
            Allocation: ₹{virtualMargin.toLocaleString('en-IN')}
          </div>
        </div>

        {/* Total Net P&L */}
        <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="font-semibold">Net P&L</span>
            {Number(netPnl) >= 0 ? (
              <TrendingUp size={15} className="text-emerald-400" />
            ) : (
              <TrendingDown size={15} className="text-rose-400" />
            )}
          </div>
          <div
            className={`text-lg sm:text-2xl font-bold font-mono tabular-nums ${
              Number(netPnl) >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {Number(netPnl) >= 0 ? '+' : ''}₹{Number(netPnl).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
          </div>
          <div className="text-[10px] text-slate-500 font-mono">
            {totalTrades} closed positions
          </div>
        </div>

        {/* Win Rate */}
        <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="font-semibold">Win Rate</span>
            <Award size={15} className="text-amber-400" />
          </div>
          <div className="text-lg sm:text-2xl font-bold font-mono text-amber-300 tabular-nums">
            {winRate}%
          </div>
          <div className="text-[10px] text-slate-500 font-mono">
            {winCount} Wins • {lossCount} Losses
          </div>
        </div>

        {/* Open Positions */}
        <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg space-y-1">
          <div className="flex items-center justify-between text-slate-400 text-xs">
            <span className="font-semibold">Active Trades</span>
            <Activity size={15} className="text-indigo-400" />
          </div>
          <div className="text-lg sm:text-2xl font-bold font-mono text-indigo-300 tabular-nums">
            {openPositions.length}
          </div>
          <div className="text-[10px] text-slate-500 font-mono">
            Profit Factor: {profitFactor}
          </div>
        </div>
      </div>

      {/* Dashboard Subtabs Navigation */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
        <button
          onClick={() => setActiveTab('portfolio')}
          className={`flex items-center gap-2 px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all ${
            activeTab === 'portfolio'
              ? 'bg-blue-600 text-white shadow-md'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Layers size={14} />
          <span>Positions & Trades ({trades.length})</span>
        </button>

        <button
          onClick={() => setActiveTab('saved')}
          className={`flex items-center gap-2 px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all ${
            activeTab === 'saved'
              ? 'bg-blue-600 text-white shadow-md'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Bookmark size={14} />
          <span>Saved Debates ({savedDebates.length})</span>
        </button>
      </div>

      {/* Tab 1: Positions & Trades */}
      {activeTab === 'portfolio' && (
        <div className="space-y-4">
          {/* Active Positions Section */}
          {openPositions.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span>Live Open Positions ({openPositions.length})</span>
              </h3>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {openPositions.map((pos) => (
                  <div
                    key={pos.id}
                    className="p-4 rounded-xl bg-[#0D131F] border border-blue-500/40 shadow-lg space-y-3"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-white text-xs font-mono">
                        {pos.option_symbol || `${pos.symbol} ${pos.strike} ${pos.option_type}`}
                      </span>
                      <span className="text-[10px] font-bold uppercase px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800">
                        1 Lot ({pos.lots || 1})
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs font-mono tabular-nums">
                      <div>
                        <span className="text-slate-400 text-[10px]">Entry LTP</span>
                        <div className="text-slate-200 font-bold">₹{pos.entry_price}</div>
                      </div>
                      <div>
                        <span className="text-slate-400 text-[10px]">Target / SL</span>
                        <div className="text-emerald-400 font-bold">
                          ₹{pos.target_price} / <span className="text-rose-400">₹{pos.stop_loss}</span>
                        </div>
                      </div>
                    </div>

                    {onExitTrade && (
                      <button
                        onClick={() => onExitTrade(pos.id)}
                        className="w-full py-1.5 px-3 rounded-lg bg-rose-950 hover:bg-rose-900 border border-rose-800 text-rose-300 text-xs font-bold transition-colors"
                      >
                        Square Off Position
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* All Trades Table */}
          <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl overflow-hidden shadow-xl">
            <div className="p-4 border-b border-slate-800 flex items-center justify-between">
              <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                Paper Trade Execution Log
              </h3>
              {onOpenManualTrade && (
                <button
                  onClick={() => onOpenManualTrade()}
                  className="px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-colors"
                >
                  + New 1-Lot Paper Trade
                </button>
              )}
            </div>

            {trades.length === 0 ? (
              <div className="p-10 text-center text-slate-500 text-xs">
                No trades recorded yet. Run an AI debate or place a manual paper trade.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono tabular-nums">
                  <thead className="bg-[#080C14] text-slate-400 text-[11px] border-b border-slate-800">
                    <tr>
                      <th className="py-2.5 px-4 font-semibold">ID</th>
                      <th className="py-2.5 px-4 font-semibold">Contract</th>
                      <th className="py-2.5 px-4 font-semibold">Qty</th>
                      <th className="py-2.5 px-4 font-semibold">Entry</th>
                      <th className="py-2.5 px-4 font-semibold">Exit</th>
                      <th className="py-2.5 px-4 font-semibold">P&L</th>
                      <th className="py-2.5 px-4 font-semibold">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {trades.slice(0, 15).map((t) => {
                      const isWin = Number(t.pnl) > 0;
                      const isLoss = Number(t.pnl) < 0;
                      return (
                        <tr key={t.id} className="hover:bg-slate-900/40 transition-colors">
                          <td className="py-2.5 px-4 text-slate-500">#{t.id}</td>
                          <td className="py-2.5 px-4 font-bold text-white">
                            {t.option_symbol || `${t.symbol} ${t.strike} ${t.option_type}`}
                          </td>
                          <td className="py-2.5 px-4 text-slate-300">{t.quantity || 25} (1 lot)</td>
                          <td className="py-2.5 px-4 text-slate-300">₹{t.entry_price}</td>
                          <td className="py-2.5 px-4 text-slate-300">{t.exit_price ? `₹${t.exit_price}` : '—'}</td>
                          <td className="py-2.5 px-4 font-bold">
                            {t.pnl != null ? (
                              <span className={isWin ? 'text-emerald-400' : isLoss ? 'text-rose-400' : 'text-slate-400'}>
                                {Number(t.pnl) >= 0 ? '+' : ''}₹{Number(t.pnl).toFixed(2)}
                              </span>
                            ) : (
                              '—'
                            )}
                          </td>
                          <td className="py-2.5 px-4">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                              t.status === 'OPEN'
                                ? 'bg-blue-950 text-blue-300 border border-blue-800'
                                : isWin
                                ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                                : 'bg-rose-950 text-rose-300 border border-rose-800'
                            }`}>
                              {t.status}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 2: Saved Debates */}
      {activeTab === 'saved' && (
        <div className="space-y-3">
          {savedDebates.length === 0 ? (
            <div className="bg-[#0D131F] border border-slate-800 rounded-2xl p-12 text-center space-y-2">
              <Bookmark size={32} className="mx-auto text-slate-600" />
              <h4 className="text-sm font-bold text-slate-300">No Bookmarked Debates</h4>
              <p className="text-xs text-slate-500 max-w-sm mx-auto">
                While inspecting an AI debate in the Live Arena, click the <span className="text-amber-400">"Save"</span> button to pin setups and revisit them here.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
              {savedDebates.map((item, idx) => {
                const judge = item.judge || item.judge_verdict || {};
                const verdict = judge.verdict || 'NO_TRADE';
                const isCall = verdict.includes('CE') || verdict.includes('CALL');
                const isPut = verdict.includes('PE') || verdict.includes('PUT');

                return (
                  <div
                    key={item.id || idx}
                    className="p-4 rounded-2xl bg-[#0D131F] border border-slate-800 hover:border-slate-700 transition-all space-y-3 shadow-lg"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold text-white text-xs font-mono">
                        {item.index_symbol || item.symbol || 'NIFTY'}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                        isCall
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : isPut
                          ? 'bg-rose-950 text-rose-400 border border-rose-800'
                          : 'bg-slate-900 text-amber-400 border border-slate-700'
                      }`}>
                        {verdict}
                      </span>
                    </div>

                    <p className="text-xs text-slate-300 line-clamp-2 italic">
                      "{judge.reasoning || judge.rationale || 'Adversarial consensus verdict'}"
                    </p>

                    <div className="flex items-center justify-between text-[11px] text-slate-500 font-mono pt-2 border-t border-slate-800/80">
                      <span>{formatIST(item.timestamp_ist || item.created_at)}</span>
                      {onSelectDebate && (
                        <button
                          onClick={() => onSelectDebate(item)}
                          className="flex items-center gap-1 text-blue-400 hover:text-blue-300 font-semibold"
                        >
                          <span>Inspect</span>
                          <ChevronRight size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
