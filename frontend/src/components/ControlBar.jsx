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
  const isAngelConnected = systemStatus?.angel_connected ?? true;

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

  return (
    <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-3 sm:p-5 shadow-xl space-y-3 sm:space-y-4">
      {/* Top Row: Index Switcher, Spot Price & Telemetry Pill */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3 sm:gap-4">
        {/* Left: Index Selector & Active Spot */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          {/* Index Selector Segmented Control */}
          <div className="flex items-center bg-[#080C14] p-1 rounded-xl border border-slate-800 w-full sm:w-auto">
            <button
              onClick={() => onSelectIndex && onSelectIndex('NIFTY')}
              className={`flex-1 sm:flex-initial flex items-center justify-center gap-1.5 sm:gap-2 px-3 sm:px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${
                selectedIndex === 'NIFTY'
                  ? 'bg-blue-600 text-white shadow-md shadow-blue-600/30'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <TrendingUp size={14} />
              <span>NIFTY 50</span>
              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-black/40 text-blue-200 tabular-nums">
                Lot 25
              </span>
            </button>
            <button
              onClick={() => onSelectIndex && onSelectIndex('BANKNIFTY')}
              className={`flex-1 sm:flex-initial flex items-center justify-center gap-1.5 sm:gap-2 px-3 sm:px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${
                selectedIndex === 'BANKNIFTY'
                  ? 'bg-blue-600 text-white shadow-md shadow-blue-600/30'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Activity size={14} />
              <span>BANK NIFTY</span>
              <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-black/40 text-blue-200 tabular-nums">
                Lot 15
              </span>
            </button>
          </div>

          {/* Dynamic Active Spot Display */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-[#080C14] border border-slate-800 text-xs">
            <span className="font-semibold text-slate-400">
              {selectedIndex === 'BANKNIFTY' ? 'BANK NIFTY Spot:' : 'NIFTY 50 Spot:'}
            </span>
            <span className="text-sm font-bold font-mono text-blue-300 tabular-nums">
              ₹{Number(activeSpot).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
        </div>

        {/* Right: Angel One SmartAPI Telemetry & Scheduler Timer */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Angel One SmartAPI Connection Pill */}
          <div
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-xs font-semibold ${
              isAngelConnected
                ? 'bg-emerald-950/40 border-emerald-700/60 text-emerald-300'
                : 'bg-amber-950/40 border-amber-700/60 text-amber-300'
            }`}
            title="Angel One SmartAPI Live Real-Time Feed"
          >
            <span className="relative flex h-2 w-2">
              {isAngelConnected && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              )}
              <span
                className={`relative inline-flex rounded-full h-2 w-2 ${
                  isAngelConnected ? 'bg-emerald-400' : 'bg-amber-400'
                }`}
              />
            </span>
            {isAngelConnected ? <Wifi size={13} /> : <WifiOff size={13} />}
            <span>Angel One SmartAPI: {isAngelConnected ? 'Live Feed' : 'Cached Feed'}</span>
          </div>

          {/* Scheduler Countdown Pill */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#080C14] border border-slate-800 text-xs">
            <Radio size={13} className={isSchedulerRunning ? 'text-emerald-400 animate-pulse' : 'text-amber-400'} />
            <span className="text-slate-400 font-medium">Auto-Run:</span>
            <span className="font-mono font-bold text-amber-300 tabular-nums">
              {isSchedulerRunning ? formatCountdown(countdownSeconds) : 'Paused'}
            </span>
          </div>
        </div>
      </div>

      {/* Bottom Row: Guardrail Summary & Action Buttons */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3 pt-3 border-t border-slate-800/80">
        {/* Guardrail Rules Badge */}
        <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 rounded-xl bg-blue-950/40 border border-blue-600/30 text-xs">
          <ShieldCheck size={14} className="text-emerald-400 flex-shrink-0" />
          <span className="font-bold text-blue-300 font-mono">Strict 1-Lot (Lot: {lotSize})</span>
          <span className="text-slate-600">•</span>
          <span className="font-bold text-emerald-400 font-mono">₹5,000 Max Budget</span>
          <span className="text-slate-600">•</span>
          <span className="font-bold text-rose-400 font-mono">Max SL ₹700</span>
          <span className="text-slate-600">•</span>
          <span className="font-bold text-amber-300 font-mono">Max Target ₹1,000</span>
        </div>

        {/* Action Buttons */}
        <div className="grid grid-cols-1 sm:grid-cols-3 lg:flex lg:items-center gap-2 sm:gap-3 w-full lg:w-auto">
          {/* Manual Paper Trade Button */}
          {onOpenManualTrade && (
            <button
              onClick={() => onOpenManualTrade()}
              className="flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl font-semibold text-xs bg-indigo-600 hover:bg-indigo-500 text-white shadow-md transition-colors w-full lg:w-auto"
              title="Open Guardrail-Enforced Manual Paper Trade Ticket"
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
              flex items-center justify-center gap-2 px-4 py-2 rounded-xl font-bold text-xs
              transition-all shadow-md w-full lg:w-auto
              ${
                runningDebate
                  ? 'bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700'
                  : 'bg-blue-600 hover:bg-blue-500 text-white shadow-blue-600/20'
              }
            `}
          >
            {runningDebate ? (
              <>
                <Loader2 size={14} className="animate-spin text-blue-300" />
                <span>Debating {selectedIndex}…</span>
              </>
            ) : (
              <>
                <Play size={14} className="fill-white" />
                <span>Trigger AI Debate</span>
              </>
            )}
          </button>

          {/* Scheduler Toggle */}
          <button
            onClick={isSchedulerRunning ? onStopScheduler : onStartScheduler}
            className={`
              flex items-center justify-center gap-2 px-3.5 py-2 rounded-xl font-medium text-xs
              transition-colors border w-full lg:w-auto
              ${
                isSchedulerRunning
                  ? 'bg-emerald-950/60 border-emerald-700/80 text-emerald-300 hover:bg-emerald-900/60'
                  : 'bg-slate-800/80 border-slate-700 text-slate-300 hover:bg-slate-700'
              }
            `}
          >
            {isSchedulerRunning ? (
              <>
                <Square size={13} className="fill-emerald-400 text-emerald-400" />
                <span>Pause Auto-Debate</span>
              </>
            ) : (
              <>
                <Play size={13} />
                <span>Start Auto-Debate</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
