import React, { useState, useEffect } from 'react';
import {
  Zap,
  Play,
  Loader2,
  ShieldCheck,
  Radio,
  Square,
  Activity,
  Layers,
  Wallet,
  Clock,
  TrendingUp,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { formatTimeOnlyIST, formatCountdown } from '../utils/time';

export default function ControlBar({
  systemStatus,
  schedulerStatus,
  selectedIndex = 'NIFTY',
  onSelectIndex,
  runningDebate,
  loadingStatus,
  onRunDebate,
  onStartScheduler,
  onStopScheduler,
  onOpenManualTrade,
}) {
  const [currentIST, setCurrentIST] = useState(new Date());
  const [countdownSeconds, setCountdownSeconds] = useState(
    systemStatus?.countdown_seconds ?? 300
  );

  const isSchedulerRunning =
    schedulerStatus?.is_running ?? schedulerStatus?.running ?? systemStatus?.scheduler_running ?? false;
  const isMarketOpen = systemStatus?.market_open ?? false;
  const dhanStatus = (systemStatus?.dhan_status || 'FALLBACK').toUpperCase();

  // Sync countdown when schedulerStatus or systemStatus changes
  useEffect(() => {
    if (schedulerStatus?.next_run) {
      const targetTime = new Date(schedulerStatus.next_run).getTime();
      const now = Date.now();
      const diffSec = Math.max(0, Math.floor((targetTime - now) / 1000));
      setCountdownSeconds(diffSec);
    } else if (systemStatus?.countdown_seconds != null) {
      setCountdownSeconds(systemStatus.countdown_seconds);
    }
  }, [schedulerStatus?.next_run, systemStatus?.countdown_seconds]);

  // Real-time ticking timer (ticks IST clock and countdown seconds)
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentIST(new Date());
      setCountdownSeconds((prev) => {
        if (!isSchedulerRunning) return 0;
        return prev > 0 ? prev - 1 : 300;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [isSchedulerRunning]);

  const spotNifty = systemStatus?.nifty_spot ?? 24986.40;
  const spotBankNifty = systemStatus?.banknifty_spot ?? 52140.80;
  const activeSpot = selectedIndex === 'BANKNIFTY' ? spotBankNifty : spotNifty;
  const lotSize = selectedIndex === 'BANKNIFTY' ? 15 : 25;

  // Determine Dhan Connection Badge details
  let dhanBadgeLabel = 'Dhan Data: Simulated / Disconnected';
  let dhanBadgeClass = 'bg-slate-900 border-slate-700 text-slate-300';
  let dhanDotClass = 'bg-slate-500';
  let isDhanLive = false;

  if (dhanStatus === 'LIVE' || dhanStatus === 'REAL_FEED') {
    dhanBadgeLabel = 'Real Market Feed (NSE)';
    dhanBadgeClass = 'bg-emerald-950/80 border-emerald-700/60 text-emerald-300';
    dhanDotClass = 'bg-emerald-400';
    isDhanLive = true;
  } else if (dhanStatus === 'CACHED' || dhanStatus === 'FALLBACK') {
    dhanBadgeLabel = 'Real Market Feed: Cached Close';
    dhanBadgeClass = 'bg-blue-950/80 border-blue-700/60 text-blue-300';
    dhanDotClass = 'bg-blue-400';
  }

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-3 sm:p-5 shadow-xl space-y-3 sm:space-y-4">
      {/* Top Row: Brand & Index Selector & Live Clocks */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3 sm:gap-4">
        {/* Brand & Index Switcher */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
          <div className="flex items-center gap-3">
            <div className="relative flex-shrink-0">
              <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-blue-600 flex items-center justify-center text-white shadow-md">
                <Zap size={20} />
              </div>
              <div className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-emerald-400 rounded-full border-2 border-slate-900" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <h1 className="text-base sm:text-lg font-bold tracking-tight text-white">
                  NiftyDebateAI
                </h1>
                <span className="text-[9px] sm:text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800">
                  Paper Trading
                </span>
                <span className="text-[9px] sm:text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800">
                  1-Lot Guardrail
                </span>
              </div>
              <p className="text-[10px] sm:text-[11px] text-slate-400 font-medium tracking-wide">
                Dhan Live Data • Multi-Agent Debate • IST Sync
              </p>
            </div>
          </div>

          {/* Index Selector Segmented Control - Full width on mobile */}
          <div className="flex items-center bg-slate-950 p-1 rounded-xl border border-slate-800 w-full sm:w-auto">
            <button
              onClick={() => onSelectIndex && onSelectIndex('NIFTY')}
              className={`flex-1 sm:flex-initial flex items-center justify-center gap-1.5 sm:gap-2 px-3 sm:px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all ${
                selectedIndex === 'NIFTY'
                  ? 'bg-blue-600 text-white shadow'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <TrendingUp size={14} />
              <span>NIFTY 50</span>
              <span className="text-[9px] sm:text-[10px] font-mono px-1.5 py-0.2 rounded bg-black/40 text-blue-200">
                Lot 25
              </span>
            </button>
            <button
              onClick={() => onSelectIndex && onSelectIndex('BANKNIFTY')}
              className={`flex-1 sm:flex-initial flex items-center justify-center gap-1.5 sm:gap-2 px-3 sm:px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all ${
                selectedIndex === 'BANKNIFTY'
                  ? 'bg-blue-600 text-white shadow'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Activity size={14} />
              <span>BANK NIFTY</span>
              <span className="text-[9px] sm:text-[10px] font-mono px-1.5 py-0.2 rounded bg-black/40 text-blue-200">
                Lot 15
              </span>
            </button>
          </div>
        </div>

        {/* Live IST Time & Scheduler Countdown */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Live Indian Standard Time */}
          <div className="flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] sm:text-xs">
            <Clock size={13} className="text-blue-400" />
            <span className="text-slate-400 font-medium">IST:</span>
            <span className="font-mono font-bold text-slate-200">
              {formatTimeOnlyIST(currentIST)}
            </span>
          </div>

          {/* Real-Time Next Scheduled Debate Countdown Timer */}
          <div className="flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] sm:text-xs">
            <Radio size={13} className={isSchedulerRunning ? 'text-emerald-400 animate-pulse' : 'text-amber-400'} />
            <span className="text-slate-400 font-medium">Next:</span>
            <span className="font-mono font-bold text-amber-300">
              {isSchedulerRunning ? formatCountdown(countdownSeconds) : 'Paused'}
            </span>
          </div>

          {/* Market Status */}
          <div className={`px-2.5 py-1.5 rounded-lg border text-[11px] sm:text-xs font-bold flex items-center gap-1.5 ${
            isMarketOpen
              ? 'bg-emerald-950/60 border-emerald-800 text-emerald-400'
              : 'bg-slate-950 border-slate-800 text-slate-400'
          }`}>
            <span className={`w-2 h-2 rounded-full ${isMarketOpen ? 'bg-emerald-400 animate-ping' : 'bg-slate-500'}`} />
            <span>{isMarketOpen ? 'Market Open (NSE)' : 'Sandbox'}</span>
          </div>
        </div>
      </div>

      {/* Middle Row: Metrics & Data Feed Status */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3 pt-3 border-t border-slate-800/80">
        {/* Left: Feed Status, Active Spot & Guardrails */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Dhan Live Connection Status Indicator Badge */}
          <div
            className={`flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-3 py-1.5 rounded-lg border text-[11px] sm:text-xs font-medium ${dhanBadgeClass}`}
            title="DhanHQ REST v2 Live Connection Status"
          >
            <div className="relative flex items-center justify-center">
              <span className={`w-2 h-2 rounded-full ${dhanDotClass}`} />
              {isDhanLive && (
                <span className="absolute w-2 h-2 rounded-full bg-emerald-400 animate-ping opacity-75" />
              )}
            </div>
            {isDhanLive ? (
              <Wifi size={13} className="text-emerald-400" />
            ) : (
              <WifiOff size={13} className="text-amber-400" />
            )}
            <span className="font-semibold">{dhanBadgeLabel}</span>
          </div>

          {/* Dynamic Spot Price Display */}
          <div className="flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] sm:text-xs">
            <Activity size={13} className="text-blue-400" />
            <span className="font-semibold text-slate-400">
              {selectedIndex === 'BANKNIFTY' ? 'BANK NIFTY:' : 'NIFTY:'}
            </span>
            <span className="text-xs sm:text-sm font-bold font-mono text-blue-300">
              ₹{Number(activeSpot).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>

          {/* Position Sizing & Budget Guardrail Badge */}
          <div className="flex flex-wrap items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg bg-blue-950/60 border border-blue-600/40 text-[11px] sm:text-xs">
            <ShieldCheck size={13} className="text-emerald-400 flex-shrink-0" />
            <span className="font-bold text-blue-300 font-mono">1 Lot Max</span>
            <span className="text-slate-600">•</span>
            <span className="font-bold text-emerald-400 font-mono">₹5k Budget</span>
            <span className="text-slate-600">•</span>
            <span className="font-bold text-rose-400 font-mono">Max SL ₹700</span>
            <span className="text-slate-600">•</span>
            <span className="font-bold text-amber-300 font-mono">Max Tgt ₹1k</span>
          </div>
        </div>

        {/* Right: Action Buttons - Mobile Full Width Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 lg:flex lg:items-center gap-2 sm:gap-3 w-full lg:w-auto">
          {/* Manual Trade Button */}
          {onOpenManualTrade && (
            <button
              onClick={() => onOpenManualTrade()}
              className="flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl font-semibold text-xs bg-indigo-600 hover:bg-indigo-500 text-white shadow transition-colors w-full lg:w-auto"
              title="Open Manual Paper Trade Modal"
            >
              <Layers size={14} />
              <span>Manual Paper Trade</span>
            </button>
          )}

          {/* Run Debate Button */}
          <button
            onClick={onRunDebate}
            disabled={runningDebate}
            className={`
              flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl font-semibold text-xs
              transition-colors shadow w-full lg:w-auto
              ${runningDebate
                ? 'bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700'
                : 'bg-blue-600 hover:bg-blue-500 text-white'
              }
            `}
          >
            {runningDebate ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Debating {selectedIndex}…</span>
              </>
            ) : (
              <>
                <Play size={14} />
                <span>Debate {selectedIndex === 'BANKNIFTY' ? 'Bank Nifty' : 'Nifty 50'}</span>
              </>
            )}
          </button>

          {/* Scheduler Toggle */}
          <button
            onClick={isSchedulerRunning ? onStopScheduler : onStartScheduler}
            className={`
              flex items-center justify-center gap-2 px-3 py-2 rounded-xl font-medium text-xs
              transition-colors border w-full lg:w-auto
              ${isSchedulerRunning
                ? 'bg-emerald-950 border-emerald-700 text-emerald-300 hover:bg-emerald-900'
                : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
              }
            `}
          >
            {isSchedulerRunning ? (
              <>
                <Square size={13} />
                <span>Pause Auto</span>
              </>
            ) : (
              <>
                <Play size={13} />
                <span>Auto-Debate</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
