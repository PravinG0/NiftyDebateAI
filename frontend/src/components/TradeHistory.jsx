import React, { useState } from 'react';
import {
  ArrowUpCircle,
  ArrowDownCircle,
  MinusCircle,
  TrendingUp,
  TrendingDown,
  Clock,
  ChevronLeft,
  ChevronRight,
  Activity,
  Target,
  Shield,
  Eye,
  Layers,
  LogOut,
  Radio,
  Loader2,
  CheckCircle,
  AlertTriangle,
  X,
} from 'lucide-react';
import DebateModal from './DebateModal';
import OptionChainBoard from './OptionChainBoard';
import { formatIST, formatShortIST } from '../utils/time';

const TABS = [
  { id: 'chain', label: 'Option Chain & Trade', icon: Layers },
  { id: 'active', label: 'Active Positions', icon: Activity },
  { id: 'history', label: 'Trade History', icon: Clock },
  { id: 'debates', label: 'AI Debate History', icon: Eye },
];

function formatDate(ts) {
  return formatIST(ts);
}

function formatShortDate(ts) {
  return formatIST(ts);
}

function formatPnl(pnl) {
  if (pnl == null) return '—';
  const val = Number(pnl);
  const formatted = Math.abs(val).toFixed(2);
  if (val > 0) return <span className="text-emerald-400 font-bold">+₹{formatted}</span>;
  if (val < 0) return <span className="text-rose-400 font-bold">-₹{formatted}</span>;
  return <span className="text-slate-400 font-bold">₹0.00</span>;
}

function VerdictPill({ verdict }) {
  const raw = typeof verdict === 'string'
    ? verdict
    : (verdict?.verdict || verdict?.decision || verdict?.trade_type || verdict?.type || verdict?.action || '');
  const v = String(raw || '').toUpperCase();

  if (v.includes('CE') || v.includes('CALL')) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded text-xs font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">
        <ArrowUpCircle size={13} /> CALL (CE)
      </span>
    );
  }
  if (v.includes('PE') || v.includes('PUT')) {
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded text-xs font-bold bg-rose-950 text-rose-400 border border-rose-800">
        <ArrowDownCircle size={13} /> PUT (PE)
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded text-xs font-bold bg-slate-800 text-amber-400 border border-slate-700">
      <MinusCircle size={13} /> NO TRADE
    </span>
  );
}

function ActiveTradesTab({ trades, onExitTrade, squareOffNotice, onDismissNotice }) {
  const [squaringOffId, setSquaringOffId] = useState(null);

  const handleSquareOff = async (id) => {
    if (!onExitTrade) return;
    setSquaringOffId(id);
    try {
      await onExitTrade(id);
    } finally {
      setSquaringOffId(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* Realized Profit / Loss Square-Off Notice Banner */}
      {squareOffNotice && (
        <div
          className={`p-4 rounded-xl border flex items-start justify-between gap-3 shadow-lg ${
            Number(squareOffNotice.pnl || 0) >= 0
              ? 'bg-emerald-950/80 border-emerald-500 text-emerald-200'
              : 'bg-rose-950/80 border-rose-500 text-rose-200'
          }`}
        >
          <div className="flex items-start gap-3">
            {Number(squareOffNotice.pnl || 0) >= 0 ? (
              <CheckCircle size={22} className="text-emerald-400 mt-0.5 flex-shrink-0" />
            ) : (
              <AlertTriangle size={22} className="text-rose-400 mt-0.5 flex-shrink-0" />
            )}
            <div>
              <div className="font-bold text-sm">
                Position Squared Off:{' '}
                <span className="font-mono text-white">
                  {squareOffNotice.option_symbol || squareOffNotice.symbol || 'Contract'}
                </span>
              </div>
              <div className="text-xs mt-1 space-x-3 font-mono">
                <span>
                  Exit LTP:{' '}
                  <strong className="text-white">
                    ₹{Number(squareOffNotice.exit_price || 0).toFixed(2)}
                  </strong>
                </span>
                <span>•</span>
                <span>
                  Realized {Number(squareOffNotice.pnl || 0) >= 0 ? 'Profit' : 'Loss'}:{' '}
                  <strong
                    className={
                      Number(squareOffNotice.pnl || 0) >= 0
                        ? 'text-emerald-300 text-sm'
                        : 'text-rose-300 text-sm'
                    }
                  >
                    {Number(squareOffNotice.pnl || 0) >= 0 ? '+' : ''}₹
                    {Number(squareOffNotice.pnl || 0).toFixed(2)} (
                    {squareOffNotice.pnl_percent || 0}%)
                  </strong>
                </span>
              </div>
            </div>
          </div>

          {onDismissNotice && (
            <button
              onClick={onDismissNotice}
              className="p-1 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
              title="Dismiss Notice"
            >
              <X size={16} />
            </button>
          )}
        </div>
      )}

      {/* Position Cards */}
      {!trades || trades.length === 0 ? (
        <div className="text-center py-12 text-slate-500">
          <Activity size={32} className="mx-auto mb-3 opacity-40 text-slate-400" />
          <p className="text-sm font-medium">No open positions currently</p>
          <p className="text-xs text-slate-600 mt-1">
            Open a position via the Option Chain or trigger an AI Debate
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {trades.map((trade, i) => {
            const sym = trade.index_symbol || (trade.option_symbol?.includes('BANK') ? 'BANKNIFTY' : 'NIFTY');
            const lotSize = sym === 'BANKNIFTY' ? 15 : 25;
            const qty = trade.quantity || lotSize;
            const lots = Math.max(1, Math.round(qty / lotSize));
            const entryPrice = parseFloat(trade.entry_price) || 0;
            const livePrice = parseFloat(trade.live_price ?? trade.cmp ?? trade.entry_price) || entryPrice;
            const invested = trade.invested_amount ?? Math.round(entryPrice * qty);
            const pnl = trade.unrealized_pnl ?? trade.pnl ?? Math.round((livePrice - entryPrice) * qty * 100) / 100;
            const currentVal = trade.current_value ?? Math.round(invested + pnl);
            const pnlPercent = invested > 0 ? ((pnl / invested) * 100).toFixed(2) : '0.00';
            const isProfit = Number(pnl) > 0;
            const isManual = trade.trade_source === 'MANUAL';
            const isClosing = squaringOffId === trade.id;
            const buyTimeStr = trade.buy_time || trade.opened_at || trade.created_at || trade.timestamp;

            return (
              <div
                key={trade.id || i}
                className={`p-4 rounded-xl border bg-slate-900 shadow-md ${
                  isProfit ? 'border-emerald-600/50' : 'border-rose-600/50'
                }`}
              >
                {/* Header: Symbol, Source Badge & Buy Time */}
                <div className="flex items-start justify-between mb-3 border-b border-slate-800 pb-2.5">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-sm text-white">
                        {trade.option_symbol || trade.symbol || sym}
                      </span>
                      <span
                        className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                          isManual
                            ? 'bg-indigo-950 text-indigo-300 border border-indigo-700'
                            : 'bg-blue-950 text-blue-300 border border-blue-700'
                        }`}
                      >
                        {isManual ? 'MANUAL' : 'AI DEBATE'}
                      </span>
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                        {sym === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'}
                      </span>
                    </div>

                    {/* Buy Time in strict IST */}
                    <div className="flex items-center gap-1.5 text-[11px] text-slate-400 font-mono mt-1">
                      <Clock size={12} className="text-blue-400" />
                      <span>Buy Time:</span>
                      <span className="text-slate-200 font-semibold">{formatDate(buyTimeStr)}</span>
                    </div>
                  </div>

                  <VerdictPill verdict={trade.trade_type || trade.type || trade.side} />
                </div>

                {/* 4-Metric Grid: Entry LTP, Live Price (CMP), Stop Loss, Target */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs mb-3">
                  {/* Entry LTP */}
                  <div className="p-2 rounded-lg bg-slate-950 border border-slate-800">
                    <span className="text-slate-400 block text-[10px] uppercase font-semibold">Entry LTP</span>
                    <p className="font-mono font-bold text-slate-200 text-sm mt-0.5">
                      ₹{entryPrice.toFixed(2)}
                    </p>
                    <p className="text-[10px] text-slate-500 font-mono">
                      {lots}L ({qty} Qty)
                    </p>
                  </div>

                  {/* Live Price (CMP) with pulse indicator */}
                  <div className="p-2 rounded-lg bg-slate-950 border border-slate-800 relative">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-400 block text-[10px] uppercase font-semibold">Live Price</span>
                      <div className="flex items-center gap-1">
                        <span className="relative flex h-2 w-2">
                          <span
                            className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                              isProfit ? 'bg-emerald-400' : 'bg-rose-400'
                            }`}
                          />
                          <span
                            className={`relative inline-flex rounded-full h-2 w-2 ${
                              isProfit ? 'bg-emerald-500' : 'bg-rose-500'
                            }`}
                          />
                        </span>
                      </div>
                    </div>
                    <p
                      className={`font-mono font-bold text-sm mt-0.5 ${
                        livePrice >= entryPrice ? 'text-emerald-400' : 'text-rose-400'
                      }`}
                    >
                      ₹{livePrice.toFixed(2)}
                    </p>
                    <p className="text-[10px] text-slate-500 font-mono">
                      CMP (Real-time)
                    </p>
                  </div>

                  {/* Stop Loss */}
                  <div className="p-2 rounded-lg bg-slate-950 border border-slate-800">
                    <span className="text-slate-400 block text-[10px] uppercase font-semibold">Stop Loss</span>
                    <p className="font-mono font-bold text-rose-400 text-sm mt-0.5">
                      ₹{Number(trade.stop_loss || 0).toFixed(2)}
                    </p>
                    <p className="text-[10px] text-slate-500 font-mono">
                      ₹{Math.round((trade.stop_loss || 0) * qty).toLocaleString('en-IN')}
                    </p>
                  </div>

                  {/* Target */}
                  <div className="p-2 rounded-lg bg-slate-950 border border-slate-800">
                    <span className="text-slate-400 block text-[10px] uppercase font-semibold">Target</span>
                    <p className="font-mono font-bold text-emerald-400 text-sm mt-0.5">
                      ₹{Number(trade.target || 0).toFixed(2)}
                    </p>
                    <p className="text-[10px] text-slate-500 font-mono">
                      ₹{Math.round((trade.target || 0) * qty).toLocaleString('en-IN')}
                    </p>
                  </div>
                </div>

                {/* Sizing & Budget Summary */}
                <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 flex items-center justify-between text-xs font-mono mb-3">
                  <div>
                    <span className="text-slate-500 text-[10px] block">Invested Amount</span>
                    <span className="font-bold text-slate-200">
                      ₹{Number(invested).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="text-right">
                    <span className="text-slate-500 text-[10px] block">Current Value</span>
                    <span className="font-bold text-slate-200">
                      ₹{Number(currentVal).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                </div>

                {/* Bottom Row: Unrealized P&L & Square Off Action */}
                <div className="flex items-center justify-between pt-2 border-t border-slate-800">
                  <div>
                    <span className="text-[11px] text-slate-500 block">Unrealized P&L</span>
                    <div className="flex items-baseline gap-1.5">
                      <span className="font-mono font-black text-base">
                        {formatPnl(pnl)}
                      </span>
                      <span
                        className={`text-xs font-mono font-bold ${
                          isProfit ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        ({pnlPercent > 0 ? `+${pnlPercent}` : pnlPercent}%)
                      </span>
                    </div>
                  </div>

                  {onExitTrade && (
                    <button
                      onClick={() => handleSquareOff(trade.id)}
                      disabled={isClosing}
                      className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 disabled:bg-slate-800 text-white text-xs font-bold transition-colors shadow-sm"
                      title="Square off this active paper position at live market price"
                    >
                      {isClosing ? (
                        <>
                          <Loader2 size={13} className="animate-spin" />
                          <span>Closing…</span>
                        </>
                      ) : (
                        <>
                          <LogOut size={13} />
                          <span>Square Off</span>
                        </>
                      )}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TradeHistoryTab({ trades, summary }) {
  const [page, setPage] = useState(0);
  const perPage = 10;
  const totalPages = Math.ceil((trades?.length || 0) / perPage);
  const paged = (trades || []).slice(page * perPage, (page + 1) * perPage);

  return (
    <div className="space-y-4">
      {/* Summary Stats */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="px-4 py-3 rounded-xl bg-slate-900 border border-slate-800">
            <span className="text-xs text-slate-400">Total Net P&L</span>
            <p className="text-lg font-bold font-mono mt-0.5">
              {formatPnl(summary.total_pnl ?? summary.net_pnl)}
            </p>
          </div>
          <div className="px-4 py-3 rounded-xl bg-slate-900 border border-slate-800">
            <span className="text-xs text-slate-400">Win Rate</span>
            <p className="text-lg font-bold font-mono text-blue-400 mt-0.5">
              {summary.win_rate != null ? `${(Number(summary.win_rate) * 100).toFixed(1)}%` : '—'}
            </p>
          </div>
          <div className="px-4 py-3 rounded-xl bg-slate-900 border border-slate-800">
            <span className="text-xs text-slate-400">Total Trades</span>
            <p className="text-lg font-bold font-mono text-slate-200 mt-0.5">
              {summary.total_trades ?? trades?.length ?? 0}
            </p>
          </div>
          <div className="px-4 py-3 rounded-xl bg-slate-900 border border-slate-800">
            <span className="text-xs text-slate-400">Wins / Losses</span>
            <p className="text-lg font-bold font-mono mt-0.5">
              <span className="text-emerald-400">{summary.wins ?? 0}</span>
              <span className="text-slate-600 mx-1">/</span>
              <span className="text-rose-400">{summary.losses ?? 0}</span>
            </p>
          </div>
        </div>
      )}

      {/* Table & Mobile Cards */}
      {paged.length === 0 ? (
        <div className="text-center py-12 text-slate-500">
          <Clock size={32} className="mx-auto mb-3 opacity-40 text-slate-400" />
          <p className="text-sm font-medium">No closed trades recorded yet</p>
        </div>
      ) : (
        <>
          {/* Mobile Card List (Visible on screens < 768px) */}
          <div className="block md:hidden space-y-2.5">
            {paged.map((trade, i) => {
              const status = trade.status || (trade.exit_price ? 'CLOSED' : 'OPEN');
              const isManual = trade.trade_source === 'MANUAL';
              const sym = trade.index_symbol || (trade.option_symbol?.includes('BANK') ? 'BANKNIFTY' : 'NIFTY');
              const lotSize = sym === 'BANKNIFTY' ? 15 : 25;
              const qty = trade.quantity || lotSize;
              const pnl = trade.pnl ?? trade.realized_pnl;

              return (
                <div
                  key={`mob-hist-${trade.id || i}`}
                  className="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-2 text-xs"
                >
                  <div className="flex items-center justify-between border-b border-slate-850 pb-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-white text-sm">
                        {trade.option_symbol || trade.symbol || sym}
                      </span>
                      <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                        isManual ? 'bg-indigo-950 text-indigo-300 border border-indigo-700' : 'bg-blue-950 text-blue-300 border border-blue-700'
                      }`}>
                        {isManual ? 'MANUAL' : 'AI'}
                      </span>
                    </div>
                    <VerdictPill verdict={trade.trade_type || trade.type || trade.side} />
                  </div>

                  <div className="grid grid-cols-3 gap-2 py-1 text-center font-mono">
                    <div className="p-1.5 rounded bg-slate-900 border border-slate-800">
                      <span className="text-[10px] text-slate-500 block">Entry</span>
                      <span className="font-bold text-slate-200">₹{Number(trade.entry_price || 0).toFixed(2)}</span>
                    </div>
                    <div className="p-1.5 rounded bg-slate-900 border border-slate-800">
                      <span className="text-[10px] text-slate-500 block">Exit</span>
                      <span className="font-bold text-slate-200">{trade.exit_price ? `₹${Number(trade.exit_price).toFixed(2)}` : '—'}</span>
                    </div>
                    <div className="p-1.5 rounded bg-slate-900 border border-slate-800">
                      <span className="text-[10px] text-slate-500 block">Net P&L</span>
                      <span className="font-bold">{formatPnl(pnl)}</span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono pt-1">
                    <span>{formatDate(trade.created_at || trade.timestamp || trade.opened_at)}</span>
                    <span className="px-2 py-0.5 rounded bg-slate-850 text-slate-300 text-[10px] font-bold">
                      {status}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Desktop Table (Hidden on screens < 768px) */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-[11px] text-slate-400 uppercase tracking-wider border-b border-slate-800 bg-slate-950">
                  <th className="text-left py-3 pl-3">#</th>
                  <th className="text-left py-3">Date (IST)</th>
                  <th className="text-left py-3">Source</th>
                  <th className="text-left py-3">Symbol</th>
                  <th className="text-left py-3">Type</th>
                  <th className="text-right py-3">Entry (LTP / Total)</th>
                  <th className="text-right py-3">Exit (LTP / Total)</th>
                  <th className="text-right py-3">Net P&L</th>
                  <th className="text-right py-3 pr-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((trade, i) => {
                  const status = trade.status || (trade.exit_price ? 'CLOSED' : 'OPEN');
                  const isManual = trade.trade_source === 'MANUAL';
                  const sym = trade.index_symbol || (trade.option_symbol?.includes('BANK') ? 'BANKNIFTY' : 'NIFTY');
                  const lotSize = sym === 'BANKNIFTY' ? 15 : 25;
                  const qty = trade.quantity || lotSize;
                  const entryVal = trade.entry_price ? trade.entry_price * qty : null;
                  const exitVal = trade.exit_price ? trade.exit_price * qty : null;

                  return (
                    <tr
                      key={trade.id || i}
                      className="border-b border-slate-850 hover:bg-slate-800/40 transition-colors"
                    >
                      <td className="py-3 pl-3 text-slate-500 font-mono text-xs">
                        {page * perPage + i + 1}
                      </td>
                      <td className="py-3 text-slate-400 text-xs font-mono whitespace-nowrap">
                        {formatDate(trade.created_at || trade.timestamp || trade.opened_at)}
                      </td>
                      <td className="py-3">
                        <span
                          className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                            isManual
                              ? 'bg-indigo-950 text-indigo-300 border border-indigo-700'
                              : 'bg-blue-950 text-blue-300 border border-blue-700'
                          }`}
                        >
                          {isManual ? 'MANUAL' : 'AI DEBATE'}
                        </span>
                      </td>
                      <td className="py-3 font-mono font-semibold text-slate-200">
                        {trade.option_symbol || trade.symbol || sym}
                      </td>
                      <td className="py-3">
                        <VerdictPill verdict={trade.trade_type || trade.type || trade.side} />
                      </td>
                      <td className="py-3 text-right">
                        <div className="font-mono text-slate-200 text-xs">
                          ₹{Number(trade.entry_price || 0).toFixed(2)}
                        </div>
                        {entryVal && (
                          <div className="text-[10px] text-slate-400 font-mono">
                            ₹{Math.round(entryVal).toLocaleString('en-IN')} (1L)
                          </div>
                        )}
                      </td>
                      <td className="py-3 text-right">
                        <div className="font-mono text-slate-200 text-xs">
                          {trade.exit_price ? `₹${Number(trade.exit_price).toFixed(2)}` : '—'}
                        </div>
                        {exitVal && (
                          <div className="text-[10px] text-slate-400 font-mono">
                            ₹{Math.round(exitVal).toLocaleString('en-IN')}
                          </div>
                        )}
                      </td>
                      <td className="py-3 text-right font-mono">
                        {formatPnl(trade.pnl ?? trade.realized_pnl)}
                      </td>
                      <td className="py-3 pr-3 text-right">
                        <span
                          className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                            status === 'CLOSED' || status === 'closed'
                              ? 'bg-slate-800 text-slate-400'
                              : 'bg-blue-950 text-blue-300 border border-blue-700'
                          }`}
                        >
                          {status}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="p-1.5 rounded-lg border border-slate-700 text-slate-400 hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-xs text-slate-400 font-mono px-2">
            {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
            className="p-1.5 rounded-lg border border-slate-700 text-slate-400 hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

function DebateHistoryTab({ debates }) {
  const [selectedDebate, setSelectedDebate] = useState(null);
  const [page, setPage] = useState(0);
  const perPage = 10;
  const list = Array.isArray(debates) ? debates : [];
  const totalPages = Math.ceil(list.length / perPage);
  const paged = list.slice(page * perPage, (page + 1) * perPage);

  if (list.length === 0) {
    return (
      <div className="text-center py-12 text-slate-500">
        <Eye size={32} className="mx-auto mb-3 opacity-40 text-slate-400" />
        <p className="text-sm font-medium">No debate sessions recorded yet</p>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-2">
        {paged.map((d, i) => {
          const spot = d.spot_price || d.spot || d.nifty_spot || 0;
          const rawVerdict =
            (typeof d.decision === 'string' ? d.decision : null) ||
            (typeof d.verdict === 'string' ? d.verdict : null) ||
            d.judge?.verdict ||
            d.judge_decision?.verdict ||
            (typeof d.judge_decision === 'string' ? d.judge_decision : null) ||
            'NO_TRADE';
          const bullConf = d.bull?.confidence ?? d.bull?.confidence_score ?? d.bull_confidence ?? 0;
          const bearConf = d.bear?.confidence ?? d.bear?.confidence_score ?? d.bear_confidence ?? 0;
          const idxSym = d.index_symbol || d.symbol || 'NIFTY';

          return (
            <button
              key={d.id || i}
              onClick={() => setSelectedDebate(d)}
              className="w-full text-left p-3 sm:p-3.5 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-700 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-2.5"
            >
              <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                <span className="font-mono text-xs text-slate-500">#{d.id}</span>
                <span className="text-[10px] sm:text-xs font-bold px-1.5 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800 font-mono">
                  {idxSym === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'}
                </span>
                <span className="text-[11px] sm:text-xs text-slate-400 font-mono">{formatDate(d.timestamp_ist || d.timestamp || d.created_at)}</span>
                <span className="text-[11px] sm:text-xs font-mono font-bold text-blue-400">
                  Spot: ₹{Number(spot).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                </span>
                <div className="flex items-center gap-2 text-[11px] sm:text-xs font-mono">
                  <span className="text-emerald-400">🐂 {bullConf}/10</span>
                  <span className="text-slate-600">•</span>
                  <span className="text-rose-400">🐻 {bearConf}/10</span>
                </div>
              </div>

              <div className="flex items-center justify-between sm:justify-end gap-3 pt-1 sm:pt-0 border-t sm:border-t-0 border-slate-800">
                <VerdictPill verdict={rawVerdict} />
                <span className="text-xs text-blue-400 font-semibold hover:underline flex items-center gap-1">
                  <Eye size={13} /> View
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-3">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="p-1.5 rounded-lg border border-slate-700 text-slate-400 hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-xs text-slate-400 font-mono px-2">
            {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
            className="p-1.5 rounded-lg border border-slate-700 text-slate-400 hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      {selectedDebate && (
        <DebateModal debate={selectedDebate} onClose={() => setSelectedDebate(null)} />
      )}
    </>
  );
}

export default function TradeHistory({
  trades,
  activeTrades,
  tradesSummary,
  debateHistory,
  selectedIndex = 'NIFTY',
  onOpenManualTrade,
  onExitTrade,
  spotPrice,
  squareOffNotice,
  onDismissNotice,
}) {
  const [activeTab, setActiveTab] = useState('chain');

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-lg">
      {/* Clean Solid Tab Bar */}
      <div className="flex border-b border-slate-800 bg-slate-950 overflow-x-auto scrollbar-none">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`
                flex items-center gap-1.5 sm:gap-2 px-3.5 sm:px-5 py-2.5 sm:py-3.5 text-[11px] sm:text-xs font-bold uppercase tracking-wider transition-colors relative whitespace-nowrap
                ${isActive
                  ? 'text-white bg-slate-900'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/50'
                }
              `}
            >
              <Icon size={14} />
              <span>{tab.label}</span>
              {isActive && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-500" />
              )}
              {tab.id === 'active' && activeTrades?.length > 0 && (
                <span className="ml-1 px-1.5 py-0.2 rounded text-[9px] sm:text-[10px] font-bold bg-blue-600 text-white">
                  {activeTrades.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      <div className="p-3 sm:p-5">
        {activeTab === 'chain' && (
          <OptionChainBoard
            selectedIndex={selectedIndex}
            spotPrice={spotPrice}
            onSelectTrade={onOpenManualTrade}
          />
        )}
        {activeTab === 'active' && (
          <ActiveTradesTab
            trades={activeTrades}
            onExitTrade={onExitTrade}
            squareOffNotice={squareOffNotice}
            onDismissNotice={onDismissNotice}
          />
        )}
        {activeTab === 'history' && (
          <TradeHistoryTab
            trades={trades}
            summary={tradesSummary}
          />
        )}
        {activeTab === 'debates' && (
          <DebateHistoryTab
            debates={debateHistory}
          />
        )}
      </div>
    </div>
  );
}
