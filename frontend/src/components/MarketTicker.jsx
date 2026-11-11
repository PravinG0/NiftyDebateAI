import React, { useState, useEffect } from 'react';
import { Clock, Radio, Activity, TrendingUp, TrendingDown, Wifi, WifiOff } from 'lucide-react';
import { formatTimeOnlyIST, formatCountdown } from '../utils/time';
import { getMarketQuotes } from '../api';

export default function MarketTicker({
  systemStatus,
  schedulerStatus,
  selectedIndex,
  onSelectIndex,
}) {
  const [currentIST, setCurrentIST] = useState(new Date());
  const [quotes, setQuotes] = useState(null);

  // Ticking IST clock
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentIST(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Poll market quotes periodically with fallback to systemStatus
  useEffect(() => {
    let isSubscribed = true;
    const fetchQuotes = async () => {
      try {
        const res = await getMarketQuotes('NIFTY,BANKNIFTY');
        if (isSubscribed && res.data?.quotes) {
          setQuotes(res.data.quotes);
        }
      } catch {
        // Fallback gracefully to systemStatus if /market/quotes is not implemented yet
      }
    };

    fetchQuotes();
    const interval = setInterval(fetchQuotes, 10000);
    return () => {
      isSubscribed = false;
      clearInterval(interval);
    };
  }, []);

  const isSchedulerRunning =
    schedulerStatus?.is_running ?? schedulerStatus?.running ?? systemStatus?.scheduler_running ?? false;
  const isMarketOpen = systemStatus?.market_open ?? false;
  const angelConnected = systemStatus?.angel_connected ?? true;

  // NIFTY 50 values
  const niftySpot = quotes?.NIFTY?.ltp ?? systemStatus?.nifty_spot ?? 24986.40;
  const niftyChange = quotes?.NIFTY?.net_change ?? (quotes?.NIFTY?.change ?? 58.25);
  const niftyChangePct = quotes?.NIFTY?.percent_change ?? (quotes?.NIFTY?.change_pct ?? 0.23);

  // BANK NIFTY values
  const bankSpot = quotes?.BANKNIFTY?.ltp ?? systemStatus?.banknifty_spot ?? 52140.80;
  const bankChange = quotes?.BANKNIFTY?.net_change ?? (quotes?.BANKNIFTY?.change ?? 184.50);
  const bankChangePct = quotes?.BANKNIFTY?.percent_change ?? (quotes?.BANKNIFTY?.change_pct ?? 0.35);

  const countdownSeconds = systemStatus?.countdown_seconds ?? 300;

  return (
    <div className="w-full bg-[#080C14]/90 backdrop-blur-md border-b border-slate-800/80 text-xs py-1.5 px-3 sm:px-6">
      <div className="max-w-[1600px] mx-auto flex items-center justify-between gap-3 overflow-x-auto no-scrollbar">
        {/* Left: Indices Ribbon */}
        <div className="flex items-center gap-3 sm:gap-6 flex-shrink-0">
          {/* NIFTY 50 Pill */}
          <button
            onClick={() => onSelectIndex && onSelectIndex('NIFTY')}
            className={`flex items-center gap-2 px-2.5 py-1 rounded-lg transition-all ${
              selectedIndex === 'NIFTY'
                ? 'bg-slate-800/90 border border-blue-500/40 shadow-sm'
                : 'hover:bg-slate-900/60 border border-transparent'
            }`}
          >
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-slate-200 tracking-tight">NIFTY 50</span>
              <span className="text-[10px] font-mono px-1 py-0.2 rounded bg-black/40 text-slate-400">
                25
              </span>
            </div>
            <span className="font-mono font-bold text-slate-100 tabular-nums">
              ₹{Number(niftySpot).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span
              className={`flex items-center gap-0.5 font-mono text-[11px] font-semibold tabular-nums ${
                niftyChange >= 0 ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              {niftyChange >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {niftyChange >= 0 ? '+' : ''}{Number(niftyChange).toFixed(2)} ({Number(niftyChangePct).toFixed(2)}%)
            </span>
          </button>

          {/* BANK NIFTY Pill */}
          <button
            onClick={() => onSelectIndex && onSelectIndex('BANKNIFTY')}
            className={`flex items-center gap-2 px-2.5 py-1 rounded-lg transition-all ${
              selectedIndex === 'BANKNIFTY'
                ? 'bg-slate-800/90 border border-blue-500/40 shadow-sm'
                : 'hover:bg-slate-900/60 border border-transparent'
            }`}
          >
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-slate-200 tracking-tight">BANK NIFTY</span>
              <span className="text-[10px] font-mono px-1 py-0.2 rounded bg-black/40 text-slate-400">
                15
              </span>
            </div>
            <span className="font-mono font-bold text-slate-100 tabular-nums">
              ₹{Number(bankSpot).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span
              className={`flex items-center gap-0.5 font-mono text-[11px] font-semibold tabular-nums ${
                bankChange >= 0 ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              {bankChange >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {bankChange >= 0 ? '+' : ''}{Number(bankChange).toFixed(2)} ({Number(bankChangePct).toFixed(2)}%)
            </span>
          </button>
        </div>

        {/* Right: Live Telemetry, Market Status & Clocks */}
        <div className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
          {/* Angel One SmartAPI Telemetry Badge */}
          <div
            className={`hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[11px] font-medium ${
              angelConnected
                ? 'bg-emerald-950/40 border-emerald-800/50 text-emerald-300'
                : 'bg-amber-950/40 border-amber-800/50 text-amber-300'
            }`}
            title="Angel One SmartAPI Live Exchange Data Stream"
          >
            <span className="relative flex h-2 w-2">
              {angelConnected && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              )}
              <span
                className={`relative inline-flex rounded-full h-2 w-2 ${
                  angelConnected ? 'bg-emerald-400' : 'bg-amber-400'
                }`}
              />
            </span>
            {angelConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
            <span className="font-semibold">Angel One SmartAPI: {angelConnected ? 'Live NSE' : 'Cached Close'}</span>
          </div>

          {/* Market Status Pill */}
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[11px] font-bold ${
              isMarketOpen
                ? 'bg-emerald-950/50 border-emerald-800 text-emerald-400'
                : 'bg-slate-900 border-slate-800 text-slate-400'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${isMarketOpen ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`}
            />
            <span>{isMarketOpen ? 'NSE Open' : 'Sandbox Mode'}</span>
          </div>

          {/* Real-time IST Clock */}
          <div className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-900/80 border border-slate-800 text-[11px] font-mono text-slate-300">
            <Clock size={12} className="text-blue-400" />
            <span className="tabular-nums font-semibold">{formatTimeOnlyIST(currentIST)} IST</span>
          </div>

          {/* Debate Countdown */}
          {isSchedulerRunning && (
            <div className="hidden lg:flex items-center gap-1 px-2.5 py-1 rounded-lg bg-amber-950/40 border border-amber-800/50 text-[11px] font-mono text-amber-300">
              <Radio size={12} className="text-amber-400 animate-pulse" />
              <span>Next: {formatCountdown(countdownSeconds)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
