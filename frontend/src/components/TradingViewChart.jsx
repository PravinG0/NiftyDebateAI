import React, { useEffect, useRef, useState, useCallback, memo } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode } from 'lightweight-charts';
import { getMarketCandles } from '../api';
import { TrendingUp, Maximize2, Minimize2, ExternalLink, RefreshCw } from 'lucide-react';

const PRESET_SYMBOLS = [
  { label: 'NIFTY 50', symbol: 'NIFTY', tvSymbol: 'NSE:NIFTY' },
  { label: 'BANK NIFTY', symbol: 'BANKNIFTY', tvSymbol: 'NSE:BANKNIFTY' },
  { label: 'RELIANCE', symbol: 'RELIANCE', tvSymbol: 'NSE:RELIANCE' },
  { label: 'HDFC BANK', symbol: 'HDFCBANK', tvSymbol: 'NSE:HDFCBANK' },
];

const TIMEFRAMES = [
  { label: '5m', interval: '5m', range: '5d' },
  { label: '15m', interval: '15m', range: '5d' },
  { label: '1D', interval: '1d', range: '1mo' },
];

function TradingViewChart({ selectedIndex = 'NIFTY', onSelectIndex }) {
  const [selectedPreset, setSelectedPreset] = useState(() => {
    return PRESET_SYMBOLS.find((p) => p.symbol === selectedIndex) || PRESET_SYMBOLS[0];
  });
  const [selectedTf, setSelectedTf] = useState(TIMEFRAMES[1]); // default 15m
  const [isExpanded, setIsExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dataSource, setDataSource] = useState('angelone');
  const [lastBar, setLastBar] = useState(null);
  const [hoverBar, setHoverBar] = useState(null);

  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const requestIdRef = useRef(0);
  const isMountedRef = useRef(false);

  // Sync index preset when parent index changes
  useEffect(() => {
    const matched = PRESET_SYMBOLS.find((p) => p.symbol === selectedIndex);
    if (matched && matched.symbol !== selectedPreset.symbol) {
      setSelectedPreset(matched);
    }
  }, [selectedIndex, selectedPreset.symbol]);

  // Fetch candle data and update chart (with race condition protection and optional silent mode)
  const loadCandleData = useCallback(async (presetParam, tfParam, isSilentParam = false) => {
    // Defensively handle when called from event handlers (e.g. onClick={loadCandleData})
    const preset = (presetParam && typeof presetParam === 'object' && presetParam.symbol) ? presetParam : selectedPreset;
    const tf = (tfParam && typeof tfParam === 'object' && tfParam.interval) ? tfParam : selectedTf;
    const isSilent = typeof isSilentParam === 'boolean' ? isSilentParam : false;

    const reqId = ++requestIdRef.current;
    if (!isSilent) {
      setLoading(true);
    }
    try {
      const res = await getMarketCandles(preset.symbol, tf.interval, tf.range);

      // Discard stale out-of-order responses or if chart unmounted
      if (reqId !== requestIdRef.current || !seriesRef.current) return;
      if (res.data?.symbol && res.data.symbol.toUpperCase() !== preset.symbol.toUpperCase()) return;

      const rawCandles = res.data?.candles || [];
      if (rawCandles.length > 0) {
        const sorted = [...rawCandles].sort((a, b) => a.time - b.time);
        const clean = [];
        let lastTime = -1;
        for (const c of sorted) {
          if (
            c &&
            typeof c.time === 'number' &&
            c.time > lastTime &&
            c.open != null &&
            c.high != null &&
            c.low != null &&
            c.close != null &&
            !isNaN(c.open) &&
            !isNaN(c.high) &&
            !isNaN(c.low) &&
            !isNaN(c.close)
          ) {
            clean.push({
              time: c.time,
              open: Number(c.open),
              high: Math.max(Number(c.high), Number(c.open), Number(c.close)),
              low: Math.min(Number(c.low), Number(c.open), Number(c.close)),
              close: Number(c.close),
            });
            lastTime = c.time;
          }
        }
        if (clean.length > 0 && seriesRef.current && reqId === requestIdRef.current) {
          seriesRef.current.setData(clean);
          chartRef.current?.timeScale().fitContent();

          const latest = clean[clean.length - 1];
          const prev = clean.length > 1 ? clean[clean.length - 2] : latest;
          const change = latest.close - prev.close;
          const changePct = prev.close > 0 ? (change / prev.close) * 100 : 0;
          setLastBar({ ...latest, change, changePct });
        } else if (clean.length === 0 && seriesRef.current && reqId === requestIdRef.current) {
          seriesRef.current.setData([]);
          setLastBar(null);
        }
      } else if (seriesRef.current && reqId === requestIdRef.current) {
        seriesRef.current.setData([]);
        setLastBar(null);
      }
    } catch (err) {
      console.error('Failed to load candles:', err);
    } finally {
      if (reqId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [selectedPreset, selectedTf]);

  // Initialize TradingView Lightweight Chart ONCE on mount
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth || undefined,
      height: container.clientHeight || undefined,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#090d16' },
        textColor: '#94a3b8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.45)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.45)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: '#38bdf8',
          width: 1,
          style: 3,
          labelBackgroundColor: '#0284c7',
        },
        horzLine: {
          color: '#38bdf8',
          width: 1,
          style: 3,
          labelBackgroundColor: '#0284c7',
        },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: {
          top: 0.1,
          bottom: 0.1,
        },
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });

    chartRef.current = chart;
    seriesRef.current = series;
    isMountedRef.current = true;

    // Track crosshair hover for real-time OHLC display
    chart.subscribeCrosshairMove((param) => {
      if (param.time) {
        const data = param.seriesData.get(series);
        if (data) {
          const change = (data.close != null && data.open != null) ? data.close - data.open : 0;
          const changePct = (data.open && data.open > 0) ? (change / data.open) * 100 : 0;
          setHoverBar({ ...data, change, changePct });
          return;
        }
      }
      setHoverBar(null);
    });

    // ResizeObserver for reliable responsive resizing
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) {
        chart.applyOptions({ width, height });
        chart.timeScale().fitContent();
      }
    });
    resizeObserver.observe(container);

    // Initial data load on mount
    loadCandleData(selectedPreset, selectedTf);

    return () => {
      isMountedRef.current = false;
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []); // Mount only once

  // Reload data when preset symbol or timeframe changes (skipping initial mount)
  const hasMountedRef = useRef(false);
  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      return;
    }
    loadCandleData(selectedPreset, selectedTf);
  }, [selectedPreset, selectedTf, loadCandleData]);

  // Periodic silent background poll every 20 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      loadCandleData(selectedPreset, selectedTf, true);
    }, 20000);
    return () => clearInterval(interval);
  }, [selectedPreset, selectedTf, loadCandleData]);

  // Adjust chart size on expand toggle
  useEffect(() => {
    const container = chartContainerRef.current;
    if (container && chartRef.current) {
      setTimeout(() => {
        chartRef.current.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight,
        });
      }, 50);
    }
  }, [isExpanded]);

  const activeBar = hoverBar || lastBar;

  return (
    <div className="backdrop-blur-xl bg-slate-900/60 border border-slate-700/40 rounded-2xl overflow-hidden shadow-2xl transition-all duration-300">
      {/* Header Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 px-3 sm:px-5 py-2.5 sm:py-3 border-b border-slate-800/80 bg-slate-900/40">
        {/* Title & Live Status */}
        <div className="flex items-center justify-between sm:justify-start gap-2.5">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400 flex-shrink-0">
              <TrendingUp size={16} />
            </div>
            <div>
              <div className="flex items-center gap-1.5 sm:gap-2">
                <h2 className="text-xs sm:text-sm font-semibold text-slate-200 tracking-wide">
                  {selectedPreset.label} Chart
                </h2>
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] sm:text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Live
                </span>
              </div>
              {/* Real-time OHLC legend */}
              {activeBar && (
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] sm:text-xs font-mono mt-0.5 text-slate-400">
                  <span>O: <span className="text-slate-200">{activeBar.open?.toLocaleString()}</span></span>
                  <span>H: <span className="text-slate-200">{activeBar.high?.toLocaleString()}</span></span>
                  <span>L: <span className="text-slate-200">{activeBar.low?.toLocaleString()}</span></span>
                  <span>C: <span className={activeBar.close >= activeBar.open ? 'text-emerald-400' : 'text-rose-400'}>{activeBar.close?.toLocaleString()}</span></span>
                  {activeBar.changePct !== undefined && (
                    <span className={activeBar.change >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                      {activeBar.change >= 0 ? '+' : ''}{activeBar.changePct?.toFixed(2)}%
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Mobile Right Action shortcuts */}
          <div className="flex sm:hidden items-center gap-1">
            <button
              onClick={() => loadCandleData(selectedPreset, selectedTf)}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin text-blue-400' : ''} />
            </button>
            <button
              onClick={() => setIsExpanded(!isExpanded)}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
            >
              {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          </div>
        </div>

        {/* Center & Right Controls: Symbol Presets, Timeframes & Desktop Actions */}
        <div className="flex flex-wrap items-center justify-between sm:justify-end gap-2 overflow-x-auto max-w-full pb-1 sm:pb-0">
          {/* Symbol Presets */}
          <div className="flex items-center gap-1 bg-slate-950/60 p-0.5 sm:p-1 rounded-xl border border-slate-800">
            {PRESET_SYMBOLS.map((item) => {
              const isActive = selectedPreset.symbol === item.symbol;
              return (
                <button
                  key={item.symbol}
                  onClick={() => {
                    setSelectedPreset(item);
                    if ((item.symbol === 'NIFTY' || item.symbol === 'BANKNIFTY') && onSelectIndex) {
                      onSelectIndex(item.symbol);
                    }
                  }}
                  className={`px-2 sm:px-3 py-1 rounded-lg text-[10px] sm:text-xs font-medium whitespace-nowrap transition-all ${
                    isActive
                      ? 'bg-blue-600 text-white shadow-sm shadow-blue-500/30'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  {item.label}
                </button>
              );
            })}
          </div>

          {/* Timeframe Presets */}
          <div className="flex items-center gap-1 bg-slate-950/60 p-0.5 sm:p-1 rounded-xl border border-slate-800">
            {TIMEFRAMES.map((tf) => {
              const isActive = selectedTf.interval === tf.interval;
              return (
                <button
                  key={tf.interval}
                  onClick={() => setSelectedTf(tf)}
                  className={`px-2 sm:px-2.5 py-1 rounded-lg text-[10px] sm:text-xs font-mono transition-all ${
                    isActive
                      ? 'bg-slate-700 text-white font-semibold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {tf.label}
                </button>
              );
            })}
          </div>

          {/* Desktop Actions */}
          <div className="hidden sm:flex items-center gap-1.5">
            <button
              onClick={() => loadCandleData(selectedPreset, selectedTf)}
              title="Refresh candles"
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin text-blue-400' : ''} />
            </button>

            <a
              href={`https://www.tradingview.com/chart/?symbol=${selectedPreset.tvSymbol}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-slate-800/80 hover:bg-slate-700/80 text-blue-400 hover:text-blue-300 border border-slate-700 transition-colors"
              title="Open full chart on TradingView.com"
            >
              <span>TV</span>
              <ExternalLink size={12} />
            </a>

            <button
              onClick={() => setIsExpanded(!isExpanded)}
              title={isExpanded ? 'Normal height' : 'Expand chart'}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
            >
              {isExpanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </div>
        </div>
      </div>

      {/* Chart Canvas Area */}
      <div
        className={`w-full transition-all duration-300 relative bg-[#090d16] ${
          isExpanded ? 'h-[500px] sm:h-[650px]' : 'h-[320px] sm:h-[460px]'
        }`}
      >
        <div ref={chartContainerRef} className="h-full w-full" />

        {/* Loading Overlay */}
        {loading && (
          <div className="absolute top-3 right-3 bg-slate-900/80 backdrop-blur-md px-2.5 py-1 rounded-lg border border-slate-700 text-[11px] text-slate-400 flex items-center gap-1.5 pointer-events-none">
            <div className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            <span>Updating...</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(TradingViewChart);
