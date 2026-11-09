import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw, ArrowUpCircle, ArrowDownCircle, Layers, ShieldCheck, AlertCircle } from 'lucide-react';
import { getOptionChain } from '../api';

export default function OptionChainBoard({
  selectedIndex = 'NIFTY',
  spotPrice,
  onSelectTrade,
}) {
  const [chain, setChain] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [mobileFilter, setMobileFilter] = useState('ALL'); // 'ALL' | 'CE' | 'PE'

  const lotSize = selectedIndex === 'BANKNIFTY' ? 15 : 25;
  const strikeStep = selectedIndex === 'BANKNIFTY' ? 100 : 50;
  const budgetLimit = 5000.0;

  const fetchChain = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getOptionChain(selectedIndex, 7);
      setChain(res.data?.chain || []);
    } catch (err) {
      setError('Unable to load option chain for ' + selectedIndex);
    } finally {
      setLoading(false);
    }
  }, [selectedIndex]);

  useEffect(() => {
    fetchChain();
    const interval = setInterval(fetchChain, 15000);
    return () => clearInterval(interval);
  }, [fetchChain]);

  const handleTradeClick = (strike, optType, price) => {
    if (!onSelectTrade) return;
    const p = parseFloat(price) || 100.0;
    const maxSlPts = Math.floor((700.0 / lotSize) * 100) / 100;
    const maxTgtPts = Math.floor((1000.0 / lotSize) * 100) / 100;

    onSelectTrade({
      symbol: selectedIndex,
      strike: parseInt(strike, 10),
      optionType: optType,
      price: p,
      stopLoss: Math.max(1, Math.round((p - maxSlPts) * 10) / 10),
      target: Math.round((p + maxTgtPts) * 10) / 10,
      lotSize,
    });
  };

  const calculatedAtm = spotPrice
    ? Math.round(spotPrice / strikeStep) * strikeStep
    : null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-lg">
      {/* Top Header Bar */}
      <div className="px-3 sm:px-5 py-3 sm:py-4 border-b border-slate-800 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 bg-slate-950">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white flex-shrink-0">
            <Layers size={18} />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
              <h3 className="text-xs sm:text-sm font-bold text-white uppercase tracking-wider">
                {selectedIndex === 'BANKNIFTY' ? 'Bank Nifty' : 'Nifty 50'} Option Chain
              </h3>
              <span className="px-1.5 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                Step: {strikeStep}
              </span>
              <span className="px-1.5 py-0.5 rounded text-[9px] sm:text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800 flex items-center gap-1">
                <ShieldCheck size={11} />
                1 Lot = {lotSize} Qty
              </span>
            </div>
            <p className="text-[10px] sm:text-xs text-slate-400 mt-0.5">
              1-lot order locked • Budget limit: ₹{budgetLimit.toLocaleString('en-IN')} • Max SL ₹700 • Max Tgt ₹1k
            </p>
          </div>
        </div>

        {/* Controls: Spot & Refresh & Mobile Filter */}
        <div className="flex items-center justify-between sm:justify-end gap-2">
          {/* Mobile View Switcher */}
          <div className="flex sm:hidden items-center bg-slate-900 p-0.5 rounded-lg border border-slate-800 text-[10px] font-bold">
            <button
              onClick={() => setMobileFilter('ALL')}
              className={`px-2 py-1 rounded ${mobileFilter === 'ALL' ? 'bg-blue-600 text-white' : 'text-slate-400'}`}
            >
              All
            </button>
            <button
              onClick={() => setMobileFilter('CE')}
              className={`px-2 py-1 rounded ${mobileFilter === 'CE' ? 'bg-emerald-600 text-white' : 'text-slate-400'}`}
            >
              CE
            </button>
            <button
              onClick={() => setMobileFilter('PE')}
              className={`px-2 py-1 rounded ${mobileFilter === 'PE' ? 'bg-rose-600 text-white' : 'text-slate-400'}`}
            >
              PE
            </button>
          </div>

          <div className="text-[11px] sm:text-xs text-slate-400 flex items-center gap-1.5 bg-slate-900 px-2.5 py-1 rounded-lg border border-slate-800 font-mono">
            <span>CMP:</span>
            <span className="font-bold text-blue-300">
              ₹{Number(spotPrice || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
            </span>
          </div>

          <button
            onClick={fetchChain}
            disabled={loading}
            className="p-1.5 sm:px-2.5 sm:py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors border border-slate-700 flex items-center gap-1 text-[11px] font-semibold"
            title="Refresh Option Chain"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin text-blue-400' : ''} />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 mx-3 sm:mx-4 my-2.5 text-xs bg-rose-950 border border-rose-800 rounded-lg text-rose-200">
          {error}
        </div>
      )}

      {/* MOBILE STRIKE CARDS VIEW (Visible only on small screens < 640px) */}
      <div className="block sm:hidden divide-y divide-slate-800/80 p-2 space-y-2">
        {chain.length === 0 ? (
          <div className="py-10 text-center text-slate-500 text-xs font-sans">
            {loading ? 'Fetching option chain...' : 'No option chain data available.'}
          </div>
        ) : (
          chain.map((row) => {
            const strike = row.strike;
            const isAtm = row.is_atm || strike === calculatedAtm;
            const ceLtp = Number(row.ce?.ltp || 0);
            const peLtp = Number(row.pe?.ltp || 0);
            const ceTotal = Math.round(ceLtp * lotSize);
            const peTotal = Math.round(peLtp * lotSize);
            const ceFits = ceTotal <= budgetLimit;
            const peFits = peTotal <= budgetLimit;

            return (
              <div
                key={`mob-${strike}`}
                className={`p-2.5 rounded-xl border bg-slate-950/70 transition-all ${
                  isAtm ? 'border-blue-500/80 bg-blue-950/20' : 'border-slate-800/80'
                }`}
              >
                {/* Strike header & ATM Badge */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-sm text-white">
                      Strike {strike}
                    </span>
                    {isAtm && (
                      <span className="px-1.5 py-0.2 rounded text-[9px] font-bold bg-blue-600 text-white font-sans animate-pulse">
                        ATM
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] text-slate-400 font-mono">
                    1 Lot ({lotSize} Qty)
                  </span>
                </div>

                {/* CE & PE action cards */}
                <div className="grid grid-cols-2 gap-2">
                  {/* CE Side */}
                  {(mobileFilter === 'ALL' || mobileFilter === 'CE') && (
                    <div className={`p-2 rounded-lg border bg-slate-900 flex flex-col justify-between ${
                      mobileFilter === 'CE' ? 'col-span-2' : ''
                    } ${ceFits ? 'border-emerald-800/40' : 'border-rose-900/40'}`}>
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-bold text-emerald-400">CALL (CE)</span>
                        <span className={`text-[9px] font-bold px-1 rounded ${ceFits ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'}`}>
                          {ceFits ? '≤ ₹5k' : '> ₹5k'}
                        </span>
                      </div>
                      <div className="my-1.5 font-mono">
                        <div className="text-xs font-bold text-emerald-300">₹{ceLtp.toFixed(2)}</div>
                        <div className="text-[10px] text-slate-400 font-medium">Total: ₹{ceTotal.toLocaleString('en-IN')}</div>
                      </div>
                      <button
                        onClick={() => handleTradeClick(strike, 'CE', ceLtp)}
                        className={`w-full py-1.5 rounded text-[11px] font-bold flex items-center justify-center gap-1 shadow ${
                          ceFits ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        <ArrowUpCircle size={12} />
                        BUY CE
                      </button>
                    </div>
                  )}

                  {/* PE Side */}
                  {(mobileFilter === 'ALL' || mobileFilter === 'PE') && (
                    <div className={`p-2 rounded-lg border bg-slate-900 flex flex-col justify-between ${
                      mobileFilter === 'PE' ? 'col-span-2' : ''
                    } ${peFits ? 'border-rose-800/40' : 'border-rose-900/40'}`}>
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-bold text-rose-400">PUT (PE)</span>
                        <span className={`text-[9px] font-bold px-1 rounded ${peFits ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'}`}>
                          {peFits ? '≤ ₹5k' : '> ₹5k'}
                        </span>
                      </div>
                      <div className="my-1.5 font-mono">
                        <div className="text-xs font-bold text-rose-300">₹{peLtp.toFixed(2)}</div>
                        <div className="text-[10px] text-slate-400 font-medium">Total: ₹{peTotal.toLocaleString('en-IN')}</div>
                      </div>
                      <button
                        onClick={() => handleTradeClick(strike, 'PE', peLtp)}
                        className={`w-full py-1.5 rounded text-[11px] font-bold flex items-center justify-center gap-1 shadow ${
                          peFits ? 'bg-rose-600 hover:bg-rose-500 text-white' : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        <ArrowDownCircle size={12} />
                        BUY PE
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* DESKTOP OPTION CHAIN TABLE (Visible on sm: screens and wider) */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="bg-slate-950 text-slate-400 border-b border-slate-800 text-[11px] uppercase tracking-wider font-semibold">
              <th colSpan="4" className="py-2.5 px-4 text-center bg-emerald-950/30 text-emerald-400 border-r border-slate-800">
                CALL OPTIONS (CE)
              </th>
              <th className="py-2.5 px-4 text-center bg-slate-900 text-slate-200 border-r border-slate-800">
                STRIKE
              </th>
              <th colSpan="4" className="py-2.5 px-4 text-center bg-rose-950/30 text-rose-400">
                PUT OPTIONS (PE)
              </th>
            </tr>
            <tr className="bg-slate-950/80 text-slate-400 border-b border-slate-800 text-[10px] uppercase font-mono">
              <th className="py-2 px-3 text-right">Action</th>
              <th className="py-2 px-3 text-right">1 Lot (Total)</th>
              <th className="py-2 px-3 text-right">LTP (₹)</th>
              <th className="py-2 px-3 text-right border-r border-slate-800">OI / Chg</th>
              <th className="py-2 px-4 text-center font-bold text-white bg-slate-900 border-r border-slate-800">
                Strike
              </th>
              <th className="py-2 px-3 text-left">OI / Chg</th>
              <th className="py-2 px-3 text-left">LTP (₹)</th>
              <th className="py-2 px-3 text-left">1 Lot (Total)</th>
              <th className="py-2 px-3 text-left">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-mono">
            {chain.length === 0 ? (
              <tr>
                <td colSpan="9" className="py-12 text-center text-slate-500 font-sans">
                  {loading ? 'Fetching live option chain data…' : 'No option chain data available.'}
                </td>
              </tr>
            ) : (
              chain.map((row) => {
                const strike = row.strike;
                const isAtm = row.is_atm || strike === calculatedAtm;

                const ceLtp = Number(row.ce?.ltp || 0);
                const peLtp = Number(row.pe?.ltp || 0);

                const ceTotalAmount = Math.round(ceLtp * lotSize);
                const peTotalAmount = Math.round(peLtp * lotSize);

                const ceFitsBudget = ceTotalAmount <= budgetLimit;
                const peFitsBudget = peTotalAmount <= budgetLimit;

                return (
                  <tr
                    key={strike}
                    className={`transition-colors hover:bg-slate-800/40 ${
                      isAtm ? 'bg-blue-950/30 border-y-2 border-blue-500/60' : ''
                    }`}
                  >
                    {/* CE Buy Action */}
                    <td className="py-2.5 px-3 text-right">
                      <button
                        onClick={() => handleTradeClick(strike, 'CE', ceLtp)}
                        className={`px-2.5 py-1 rounded font-sans font-bold text-[11px] shadow transition-colors flex items-center gap-1 ml-auto ${
                          ceFitsBudget
                            ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
                            : 'bg-amber-700/80 hover:bg-amber-600 text-amber-100'
                        }`}
                        title={`Trade 1 Lot ${selectedIndex} ${strike} CE (₹${ceTotalAmount})`}
                      >
                        <ArrowUpCircle size={12} />
                        BUY CE
                      </button>
                    </td>

                    {/* CE Total Amount (1-Lot display) */}
                    <td className="py-2.5 px-3 text-right">
                      <span className="font-bold text-slate-200">
                        ₹{ceTotalAmount.toLocaleString('en-IN')}
                      </span>
                      <div className="mt-0.5">
                        {ceFitsBudget ? (
                          <span className="inline-block px-1.5 py-0.2 rounded text-[9px] font-sans font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">
                            Under ₹5k
                          </span>
                        ) : (
                          <span className="inline-block px-1.5 py-0.2 rounded text-[9px] font-sans font-bold bg-rose-950 text-rose-400 border border-rose-800">
                            Exceeds ₹5k
                          </span>
                        )}
                      </div>
                    </td>

                    {/* CE LTP */}
                    <td className="py-2.5 px-3 text-right">
                      <span className="text-sm font-bold text-emerald-400">
                        ₹{ceLtp.toFixed(2)}
                      </span>
                    </td>

                    {/* CE OI */}
                    <td className="py-2.5 px-3 text-right text-slate-400 text-[11px] border-r border-slate-800">
                      <div>{row.ce?.oi != null ? row.ce.oi.toLocaleString('en-IN') : '—'}</div>
                      {row.ce?.oi_change != null && (
                        <div
                          className={`text-[10px] ${
                            row.ce.oi_change >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {row.ce.oi_change >= 0 ? '+' : ''}
                          {row.ce.oi_change.toLocaleString('en-IN')}
                        </div>
                      )}
                    </td>

                    {/* Strike Center Column */}
                    <td
                      className={`py-2.5 px-4 text-center font-bold font-mono border-r border-slate-800 ${
                        isAtm
                          ? 'bg-blue-600 text-white text-sm shadow-inner'
                          : 'bg-slate-900/90 text-slate-100'
                      }`}
                    >
                      <div className="flex items-center justify-center gap-1">
                        <span>{strike}</span>
                        {isAtm && (
                          <span className="px-1.5 py-0.2 rounded text-[9px] bg-black/40 text-blue-200 font-sans uppercase">
                            ATM
                          </span>
                        )}
                      </div>
                    </td>

                    {/* PE OI */}
                    <td className="py-2.5 px-3 text-left text-slate-400 text-[11px]">
                      <div>{row.pe?.oi != null ? row.pe.oi.toLocaleString('en-IN') : '—'}</div>
                      {row.pe?.oi_change != null && (
                        <div
                          className={`text-[10px] ${
                            row.pe.oi_change >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {row.pe.oi_change >= 0 ? '+' : ''}
                          {row.pe.oi_change.toLocaleString('en-IN')}
                        </div>
                      )}
                    </td>

                    {/* PE LTP */}
                    <td className="py-2.5 px-3 text-left">
                      <span className="text-sm font-bold text-rose-400">
                        ₹{peLtp.toFixed(2)}
                      </span>
                    </td>

                    {/* PE Total Amount (1-Lot display) */}
                    <td className="py-2.5 px-3 text-left">
                      <span className="font-bold text-slate-200">
                        ₹{peTotalAmount.toLocaleString('en-IN')}
                      </span>
                      <div className="mt-0.5">
                        {peFitsBudget ? (
                          <span className="inline-block px-1.5 py-0.2 rounded text-[9px] font-sans font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">
                            Under ₹5k
                          </span>
                        ) : (
                          <span className="inline-block px-1.5 py-0.2 rounded text-[9px] font-sans font-bold bg-rose-950 text-rose-400 border border-rose-800">
                            Exceeds ₹5k
                          </span>
                        )}
                      </div>
                    </td>

                    {/* PE Buy Action */}
                    <td className="py-2.5 px-3 text-left">
                      <button
                        onClick={() => handleTradeClick(strike, 'PE', peLtp)}
                        className={`px-2.5 py-1 rounded font-sans font-bold text-[11px] shadow transition-colors flex items-center gap-1 ${
                          peFitsBudget
                            ? 'bg-rose-600 hover:bg-rose-500 text-white'
                            : 'bg-amber-700/80 hover:bg-amber-600 text-amber-100'
                        }`}
                        title={`Trade 1 Lot ${selectedIndex} ${strike} PE (₹${peTotalAmount})`}
                      >
                        <ArrowDownCircle size={12} />
                        BUY PE
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
