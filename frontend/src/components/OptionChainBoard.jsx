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
    onSelectTrade({
      symbol: selectedIndex,
      strike: parseInt(strike, 10),
      optionType: optType,
      price: p,
      stopLoss: Math.round(Math.max(1, p * 0.75)),
      target: Math.round(p * 1.50),
      lotSize,
    });
  };

  const calculatedAtm = spotPrice
    ? Math.round(spotPrice / strikeStep) * strikeStep
    : null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-lg">
      {/* Top Header Bar */}
      <div className="px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3 bg-slate-950">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white">
            <Layers size={18} />
          </div>
          <div>
            <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
              {selectedIndex === 'BANKNIFTY' ? 'Bank Nifty' : 'Nifty 50'} Option Chain Board
              <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                Strike Step: {strikeStep}
              </span>
              <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800 flex items-center gap-1">
                <ShieldCheck size={12} />
                Strictly 1 Lot = {lotSize} Qty
              </span>
            </h3>
            <p className="text-xs text-slate-400">
              1-lot order execution locked • Capital budget limit: ₹{budgetLimit.toLocaleString('en-IN')}
            </p>
          </div>
        </div>

        {/* Controls: Spot & Refresh */}
        <div className="flex items-center gap-3">
          <div className="text-xs text-slate-400 hidden sm:flex items-center gap-2 bg-slate-900 px-3 py-1.5 rounded-lg border border-slate-800">
            <span>Spot CMP:</span>
            <span className="font-mono font-bold text-blue-300">
              ₹{Number(spotPrice || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
            </span>
          </div>

          <button
            onClick={fetchChain}
            disabled={loading}
            className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors border border-slate-700 flex items-center gap-1.5 text-xs font-semibold"
            title="Refresh Option Chain"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 mx-4 my-3 text-xs bg-rose-950 border border-rose-800 rounded-lg text-rose-200">
          {error}
        </div>
      )}

      {/* Option Chain Table */}
      <div className="overflow-x-auto">
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
                  {loading ? 'Fetching live Dhan option chain data…' : 'No option chain data available.'}
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
                            Exceeds ₹5k Budget
                          </span>
                        )}
                      </div>
                    </td>

                    {/* CE LTP */}
                    <td className="py-2.5 px-3 text-right">
                      <span className="text-sm font-bold text-emerald-400">
                        {ceLtp.toFixed(2)}
                      </span>
                      <div className="text-[10px] text-slate-400">
                        IV: {Number(row.ce?.iv || 14.5).toFixed(1)}%
                      </div>
                    </td>

                    {/* CE OI */}
                    <td className="py-2.5 px-3 text-right text-slate-400 border-r border-slate-800 text-[11px]">
                      <div>{Number(row.ce?.oi || 0).toLocaleString('en-IN')}</div>
                      <div className={`text-[10px] ${Number(row.ce?.oi_change || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {Number(row.ce?.oi_change || 0) >= 0 ? '+' : ''}
                        {Number(row.ce?.oi_change || 0).toLocaleString('en-IN')}
                      </div>
                    </td>

                    {/* Strike */}
                    <td className={`py-2.5 px-4 text-center font-bold border-r border-slate-800 ${
                      isAtm ? 'bg-blue-600 text-white' : 'bg-slate-950 text-white'
                    }`}>
                      <div className="text-sm tracking-wide">{strike}</div>
                      {isAtm && (
                        <div className="text-[9px] font-sans font-bold uppercase tracking-widest text-blue-100">
                          ATM
                        </div>
                      )}
                    </td>

                    {/* PE OI */}
                    <td className="py-2.5 px-3 text-left text-slate-400 text-[11px]">
                      <div>{Number(row.pe?.oi || 0).toLocaleString('en-IN')}</div>
                      <div className={`text-[10px] ${Number(row.pe?.oi_change || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {Number(row.pe?.oi_change || 0) >= 0 ? '+' : ''}
                        {Number(row.pe?.oi_change || 0).toLocaleString('en-IN')}
                      </div>
                    </td>

                    {/* PE LTP */}
                    <td className="py-2.5 px-3 text-left">
                      <span className="text-sm font-bold text-rose-400">
                        {peLtp.toFixed(2)}
                      </span>
                      <div className="text-[10px] text-slate-400">
                        IV: {Number(row.pe?.iv || 14.5).toFixed(1)}%
                      </div>
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
                            Exceeds ₹5k Budget
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
