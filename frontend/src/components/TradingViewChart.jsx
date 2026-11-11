import React, { useEffect, useRef, useState, useCallback, memo } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
} from 'lightweight-charts';
import { getMarketCandles } from '../api';
import { TrendingUp, Maximize2, Minimize2, ExternalLink, RefreshCw, BarChart2 } from 'lucide-react';

const PRESET_SYMBOLS = [
  { label: 'NIFTY 50', symbol: 'NIFTY', tvSymbol: 'NSE:NIFTY' },
  { label: 'BANK NIFTY', symbol: 'BANKNIFTY', tvSymbol: 'NSE:BANKNIFTY' },
  { label: 'RELIANCE', symbol: 'RELIANCE', tvSymbol: 'NSE:RELIANCE' },
  { label: 'HDFC BANK', symbol: 'HDFCBANK', tvSymbol: 'NSE:HDFCBANK' },
];

const TIMEFRAMES = [
  { label: '1m', interval: '1m', range: '1d' },
  { label: '5m', interval: '5m', range: '5d' },
  { label: '15m', interval: '15m', range: '5d' },
  { label: '1D', interval: '1d', range: '1mo' },
];

function calculateEMA(candles, period) {
  if (!candles || candles.length === 0) return [];
  const k = 2 / (period + 1);
  const ema = [];
  let prev = candles[0].close;

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i].close;
    if (i < period) {
      prev = (prev * i + c) / (i + 1);
    } else {
      prev = c * k + prev * (1 - k);
    }
    ema.push({
      time: candles[i].time,
      value: Number(prev.toFixed(2)),
    });
  }
  return ema;
}

function TradingViewChart({ selectedIndex = 'NIFTY', onSelectIndex }) {
  const [selectedPreset, setSelectedPreset] = useState(() => {
    return PRESET_SYMBOLS.find((p) => p.symbol === selectedIndex) || PRESET_SYMBOLS[0];
  });
  const [selectedTf, setSelectedTf] = useState(TIMEFRAMES[2]); // default 15m
  const [isExpanded, setIsExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastBar, setLastBar] = useState(null);
  const [hoverBar, setHoverBar] = useState(null);

  // Indicators toggle
  const [showVolume, setShowVolume] = useState(true);
  const [showEma20, setShowEma20] = useState(true);
  const [showEma50, setShowEma50] = useState(true);

  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const ema20SeriesRef = useRef(null);
  const ema50SeriesRef = useRef(null);
  const requestIdRef = useRef(0);
  const isMountedRef = useRef(false);

  // Sync index preset when parent index changes
  useEffect(() => {
    const matched = PRESET_SYMBOLS.find((p) => p.symbol === selectedIndex);
    if (matched && matched.symbol !== selectedPreset.symbol) {
      setSelectedPreset(matched);
    }
  }, [selectedIndex, selectedPreset.symbol]);

  // Load candle data from SmartAPI feed
  const loadCandleData = useCallback(
    async (presetParam, tfParam, isSilentParam = false) => {
      const preset =
        presetParam && typeof presetParam === 'object' && presetParam.symbol
          ? presetParam
          : selectedPreset;
      const tf =
        tfParam && typeof tfParam === 'object' && tfParam.interval ? tfParam : selectedTf;
      const isSilent = typeof isSilentParam === 'boolean' ? isSilentParam : false;

      const reqId = ++requestIdRef.current;
      if (!isSilent) {
        setLoading(true);
      }

      try {
        const res = await getMarketCandles(preset.symbol, tf.interval, tf.range);

        if (reqId !== requestIdRef.current || !candleSeriesRef.current) return;
        if (res.data?.symbol && res.data.symbol.toUpperCase() !== preset.symbol.toUpperCase())
          return;

        const rawCandles = res.data?.candles || [];
        if (rawCandles.length > 0) {
          const sorted = [...rawCandles].sort((a, b) => a.time - b.time);
          const cleanCandles = [];
          const cleanVolume = [];
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
              const o = Number(c.open);
              const cl = Number(c.close);
              const h = Math.max(Number(c.high), o, cl);
              const l = Math.min(Number(c.low), o, cl);
              const vol = Number(c.volume || 0);

              cleanCandles.push({
                time: c.time,
                open: o,
                high: h,
                low: l,
                close: cl,
              });

              cleanVolume.push({
                time: c.time,
                value: vol,
                color: cl >= o ? 'rgba(16, 185, 129, 0.45)' : 'rgba(244, 63, 94, 0.45)',
              });

              lastTime = c.time;
            }
          }

          if (cleanCandles.length > 0 && candleSeriesRef.current && reqId === requestIdRef.current) {
            candleSeriesRef.current.setData(cleanCandles);

            // Volume Series
            if (volumeSeriesRef.current) {
              volumeSeriesRef.current.setData(cleanVolume);
            }

            // EMA 20 & EMA 50
            if (ema20SeriesRef.current) {
              const ema20 = calculateEMA(cleanCandles, 20);
              ema20SeriesRef.current.setData(ema20);
            }
            if (ema50SeriesRef.current) {
              const ema50 = calculateEMA(cleanCandles, 50);
              ema50SeriesRef.current.setData(ema50);
            }

            chartRef.current?.timeScale().fitContent();

            const latest = cleanCandles[cleanCandles.length - 1];
            const prev = cleanCandles.length > 1 ? cleanCandles[cleanCandles.length - 2] : latest;
            const change = latest.close - prev.close;
            const changePct = prev.close > 0 ? (change / prev.close) * 100 : 0;
            setLastBar({ ...latest, change, changePct });
          }
        }
      } catch (err) {
        console.error('Failed to load market candles:', err);
      } finally {
        if (reqId === requestIdRef.current) {
          setLoading(false);
        }
      }
    },
    [selectedPreset, selectedTf]
  );

  // Initialize chart on mount
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth || undefined,
      height: container.clientHeight || undefined,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#080C14' },
        textColor: '#94a3b8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.4)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.4)' },
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
          top: 0.08,
          bottom: 0.22,
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

    // Main Candlestick Series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });

    // Volume Histogram Series (scaled at bottom)
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: 'volume',
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: {
        top: 0.78,
        bottom: 0.01,
      },
    });

    // EMA 20 Overlay (Amber)
    const ema20Series = chart.addSeries(LineSeries, {
      color: '#F59E0B',
      lineWidth: 1.5,
      priceLineVisible: false,
      title: 'EMA 20',
    });

    // EMA 50 Overlay (Cyan/Electric Blue)
    const ema50Series = chart.addSeries(LineSeries, {
      color: '#38BDF8',
      lineWidth: 1.5,
      priceLineVisible: false,
      title: 'EMA 50',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ema20SeriesRef.current = ema20Series;
    ema50SeriesRef.current = ema50Series;
    isMountedRef.current = true;

    // Crosshair hover legend
    chart.subscribeCrosshairMove((param) => {
      if (param.time) {
        const data = param.seriesData.get(candleSeries);
        if (data) {
          const change =
            data.close != null && data.open != null ? data.close - data.open : 0;
          const changePct =
            data.open && data.open > 0 ? (change / data.open) * 100 : 0;
          setHoverBar({ ...data, change, changePct });
          return;
        }
      }
      setHoverBar(null);
    });

    // ResizeObserver
    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) {
        chart.applyOptions({ width, height });
        chart.timeScale().fitContent();
      }
    });
    resizeObserver.observe(container);

    loadCandleData(selectedPreset, selectedTf);

    return () => {
      isMountedRef.current = false;
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      ema20SeriesRef.current = null;
      ema50SeriesRef.current = null;
    };
  }, []);

  // Sync indicator visibility
  useEffect(() => {
    if (volumeSeriesRef.current) {
      volumeSeriesRef.current.applyOptions({ visible: showVolume });
    }
  }, [showVolume]);

  useEffect(() => {
    if (ema20SeriesRef.current) {
      ema20SeriesRef.current.applyOptions({ visible: showEma20 });
    }
  }, [showEma20]);

  useEffect(() => {
    if (ema50SeriesRef.current) {
      ema50SeriesRef.current.applyOptions({ visible: showEma50 });
    }
  }, [showEma50]);

  // Reload data when preset symbol or timeframe changes
  const hasMountedRef = useRef(false);
  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      return;
    }
    loadCandleData(selectedPreset, selectedTf);
  }, [selectedPreset, selectedTf, loadCandleData]);

  // Periodic silent background poll
  useEffect(() => {
    const interval = setInterval(() => {
      loadCandleData(selectedPreset, selectedTf, true);
    }, 20000);
    return () => clearInterval(interval);
  }, [selectedPreset, selectedTf, loadCandleData]);

  // Chart resize on expand toggle
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
    <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl overflow-hidden shadow-2xl transition-all duration-300">
      {/* Header Bar */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-2.5 px-3 sm:px-5 py-2.5 sm:py-3 border-b border-slate-800/80 bg-[#080C14]/50">
        {/* Title, Live Badge & Real-Time OHLC Legend */}
        <div className="flex items-center justify-between lg:justify-start gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-blue-600/10 border border-blue-500/20 flex items-center justify-center text-blue-400 flex-shrink-0">
              <TrendingUp size={16} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-xs sm:text-sm font-bold text-slate-100 tracking-wide">
                  {selectedPreset.label} Live Chart
                </h2>
                <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Angel One SmartAPI
                </span>
              </div>
              {/* Real-time OHLC Legend with Tabular Numbers */}
              {activeBar && (
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] font-mono mt-0.5 text-slate-400 tabular-nums">
                  <span>O: <span className="text-slate-200">{activeBar.open?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span></span>
                  <span>H: <span className="text-slate-200">{activeBar.high?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span></span>
                  <span>L: <span className="text-slate-200">{activeBar.low?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span></span>
                  <span>C: <span className={activeBar.close >= activeBar.open ? 'text-emerald-400' : 'text-rose-400'}>{activeBar.close?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span></span>
                  {activeBar.changePct !== undefined && (
                    <span className={`font-semibold ${activeBar.change >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {activeBar.change >= 0 ? '+' : ''}{activeBar.changePct?.toFixed(2)}%
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Mobile Refresh & Expand Actions */}
          <div className="flex lg:hidden items-center gap-1">
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

        {/* Center & Right Controls: Symbol Presets, Timeframes, Indicators, Desktop Actions */}
        <div className="flex flex-wrap items-center justify-between lg:justify-end gap-2 overflow-x-auto max-w-full pb-1 lg:pb-0">
          {/* Symbol Presets */}
          <div className="flex items-center gap-1 bg-[#080C14] p-0.5 sm:p-1 rounded-xl border border-slate-800">
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
                  className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold whitespace-nowrap transition-all ${
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

          {/* Timeframe Presets (1m, 5m, 15m, 1D) */}
          <div className="flex items-center gap-1 bg-[#080C14] p-0.5 sm:p-1 rounded-xl border border-slate-800">
            {TIMEFRAMES.map((tf) => {
              const isActive = selectedTf.interval === tf.interval;
              return (
                <button
                  key={tf.interval}
                  onClick={() => setSelectedTf(tf)}
                  className={`px-2 sm:px-2.5 py-1 rounded-lg text-[11px] font-mono transition-all tabular-nums ${
                    isActive
                      ? 'bg-slate-700 text-white font-bold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {tf.label}
                </button>
              );
            })}
          </div>

          {/* Indicators Toggle Buttons: Vol, EMA 20, EMA 50 */}
          <div className="flex items-center gap-1 bg-[#080C14] p-0.5 sm:p-1 rounded-xl border border-slate-800">
            <button
              onClick={() => setShowVolume(!showVolume)}
              className={`px-2 py-1 rounded-lg text-[10px] font-semibold transition-all ${
                showVolume
                  ? 'bg-emerald-950/80 border border-emerald-700/60 text-emerald-300'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
              title="Toggle Volume Sub-Pane"
            >
              Vol
            </button>

            <button
              onClick={() => setShowEma20(!showEma20)}
              className={`px-2 py-1 rounded-lg text-[10px] font-mono font-semibold transition-all ${
                showEma20
                  ? 'bg-amber-950/80 border border-amber-700/60 text-amber-300'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
              title="Toggle 20 Exponential Moving Average"
            >
              EMA 20
            </button>

            <button
              onClick={() => setShowEma50(!showEma50)}
              className={`px-2 py-1 rounded-lg text-[10px] font-mono font-semibold transition-all ${
                showEma50
                  ? 'bg-sky-950/80 border border-sky-700/60 text-sky-300'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
              title="Toggle 50 Exponential Moving Average"
            >
              EMA 50
            </button>
          </div>

          {/* Desktop Refresh, TV Link, Expand */}
          <div className="hidden lg:flex items-center gap-1.5">
            <button
              onClick={() => loadCandleData(selectedPreset, selectedTf)}
              title="Refresh candles"
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin text-blue-400' : ''} />
            </button>

            <a
              href={`https://www.tradingview.com/chart/?symbol=${selectedPreset.tvSymbol}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-medium bg-slate-800/80 hover:bg-slate-700/80 text-blue-400 hover:text-blue-300 border border-slate-700 transition-colors"
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
              {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          </div>
        </div>
      </div>

      {/* Chart Canvas Area */}
      <div
        className={`w-full transition-all duration-300 relative bg-[#080C14] ${
          isExpanded ? 'h-[520px] sm:h-[660px]' : 'h-[340px] sm:h-[480px]'
        }`}
      >
        <div ref={chartContainerRef} className="h-full w-full" />

        {/* Loading Overlay */}
        {loading && (
          <div className="absolute top-3 right-3 bg-[#0D131F]/90 backdrop-blur-md px-3 py-1.5 rounded-xl border border-slate-700 text-xs text-slate-300 flex items-center gap-2 pointer-events-none shadow-lg">
            <div className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            <span>Fetching Angel One Feed...</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(TradingViewChart);
