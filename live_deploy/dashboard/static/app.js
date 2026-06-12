/**
 * Live Trading Dashboard — Frontend
 * ===================================
 * Poll-based architecture (no WebSocket needed).
 * Uses lightweight-charts v4.2.3 (v4 API: addCandlestickSeries).
 * All timestamps shifted to IST via +19800 in one helper.
 */

// ============ CONSTANTS ============

const IST_OFFSET = 19800; // +5:30 in seconds
const POLL_STATE_MS = 2500;
const POLL_CANDLE_MS = 5000;
const POLL_EVENTS_MS = 3000;

// ============ STATE ============

let spotChart = null;
let spotSeries = null;
let optionChart = null;
let optionSeries = null;
let optionSlLine = null;
let optionTpLine = null;
let activeOptionType = 'ce'; // 'ce' or 'pe'
let currentState = null;
let lastEventId = null;
let syncingCharts = false;
let modalSpotChart = null;
let modalOptionChart = null;

// ============ HELPERS ============

function toIST(utcEpoch) {
    // lightweight-charts expects UTC epochs; shift for IST display
    return utcEpoch + IST_OFFSET;
}

function formatINR(val) {
    if (val == null) return '—';
    const sign = val >= 0 ? '+' : '-';
    return `${sign}₹${Math.abs(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPrice(val) {
    if (val == null) return '—';
    return `₹${Number(val).toFixed(2)}`;
}

function formatTime(isoStr) {
    if (!isoStr) return '—';
    try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } catch {
        return isoStr;
    }
}

function formatEventTime(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch {
        return '';
    }
}

async function fetchJSON(url) {
    try {
        const resp = await fetch(url);
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        console.error(`Fetch error: ${url}`, e);
        return null;
    }
}

// ============ CHART CREATION (v4 API) ============

function createChart(containerId, height) {
    const container = document.getElementById(containerId);
    if (!container) return { chart: null, series: null };
    
    // Clear previous
    container.innerHTML = '';
    
    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: height || container.clientHeight || 300,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#94a3b8',
            fontSize: 11,
            fontFamily: 'Inter, sans-serif',
        },
        grid: {
            vertLines: { color: 'rgba(55, 65, 81, 0.3)' },
            horzLines: { color: 'rgba(55, 65, 81, 0.3)' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: 'rgba(55, 65, 81, 0.5)',
        },
        timeScale: {
            borderColor: 'rgba(55, 65, 81, 0.5)',
            timeVisible: true,
            secondsVisible: false,
        },
    });
    
    const series = chart.addCandlestickSeries({
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#22c55e',
        borderDownColor: '#ef4444',
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
    });
    
    // Responsive
    const resizeObserver = new ResizeObserver(entries => {
        for (const entry of entries) {
            chart.applyOptions({
                width: entry.contentRect.width,
                height: entry.contentRect.height,
            });
        }
    });
    resizeObserver.observe(container);
    
    return { chart, series };
}

function addPriceLine(series, price, color, title, lineStyle) {
    if (!series || price == null) return null;
    return series.createPriceLine({
        price: price,
        color: color,
        lineWidth: 1,
        lineStyle: lineStyle || LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: title || '',
    });
}

function setMarkers(series, markers) {
    if (!series) return;
    try {
        // Sort markers by time
        markers.sort((a, b) => a.time - b.time);
        series.setMarkers(markers);
    } catch (e) {
        console.error('setMarkers error:', e);
    }
}

// ============ CHART SYNC ============

function setupChartSync(chart1, chart2) {
    if (!chart1 || !chart2) return;
    
    chart1.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncingCharts || !range) return;
        syncingCharts = true;
        chart2.timeScale().setVisibleLogicalRange(range);
        syncingCharts = false;
    });
    
    chart2.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncingCharts || !range) return;
        syncingCharts = true;
        chart1.timeScale().setVisibleLogicalRange(range);
        syncingCharts = false;
    });
}

// ============ STATE POLLING ============

async function pollState() {
    const data = await fetchJSON('/api/state');
    if (!data) return;
    
    currentState = data.state;
    const live = data.live;
    
    // Live dot
    const dot = document.getElementById('liveDot');
    const badge = document.getElementById('staleBadge');
    if (live) {
        dot.classList.remove('stale');
        badge.style.display = 'none';
    } else {
        dot.classList.add('stale');
        if (currentState && currentState.heartbeat_age) {
            badge.style.display = 'inline';
            badge.textContent = `Stale ${Math.round(currentState.heartbeat_age)}s`;
        }
    }
    
    if (!currentState) return;
    
    // Header metrics
    const cap = document.getElementById('capitalValue');
    cap.textContent = formatPrice(currentState.running_capital);
    
    const pnl = document.getElementById('pnlValue');
    const pnlVal = currentState.daily_pnl || 0;
    pnl.textContent = formatINR(pnlVal);
    pnl.className = 'metric-value ' + (pnlVal > 0 ? 'positive' : pnlVal < 0 ? 'negative' : 'neutral');
    
    const spot = document.getElementById('spotValue');
    spot.textContent = currentState.spot_ltp != null ? Number(currentState.spot_ltp).toFixed(2) : '—';
    
    document.getElementById('signalsValue').textContent = currentState.daily_signals || 0;
    
    // Active trade card
    const tradeCard = document.getElementById('activeTradeCard');
    const tradeGrid = document.getElementById('activeTradeGrid');
    const unrealPnl = document.getElementById('tradeUnrealizedPnl');
    
    if (currentState.active_trade) {
        tradeCard.style.display = 'block';
        const t = currentState.active_trade;
        
        // Unrealized PnL
        const optLtp = t.type === 'CE_BUY' ? currentState.ce_ltp : currentState.pe_ltp;
        if (optLtp != null) {
            const uPnl = (optLtp - t.entry) * 65 * (t.lots || 1);
            unrealPnl.textContent = formatINR(uPnl);
            unrealPnl.className = 'metric-value ' + (uPnl >= 0 ? 'positive' : 'negative');
        }
        
        // Time in trade
        let timeInTrade = '—';
        if (t.entry_time) {
            const entryMs = new Date(t.entry_time).getTime();
            const elapsed = Math.floor((Date.now() - entryMs) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            timeInTrade = `${mins}m ${secs}s`;
        }
        
        tradeGrid.innerHTML = `
            <div class="trade-field">
                <span class="trade-field-label">Type</span>
                <span class="trade-field-value">${t.type || '—'}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Symbol</span>
                <span class="trade-field-value" style="font-size:0.75rem">${(t.symbol || '—').replace('NSE:', '')}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Entry</span>
                <span class="trade-field-value">${formatPrice(t.entry)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">SL</span>
                <span class="trade-field-value" style="color:var(--red)">${formatPrice(t.sl)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">TP</span>
                <span class="trade-field-value" style="color:var(--green)">${formatPrice(t.tp)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Lots</span>
                <span class="trade-field-value">${t.lots || '—'}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">LTP</span>
                <span class="trade-field-value">${formatPrice(optLtp)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Time</span>
                <span class="trade-field-value">${timeInTrade}</span>
            </div>
        `;
    } else {
        tradeCard.style.display = 'none';
    }
    
    // Pending signals
    const chips = document.getElementById('signalChips');
    if (currentState.pending_signals && currentState.pending_signals.length > 0) {
        chips.innerHTML = currentState.pending_signals.map(sig => {
            const expires = sig.expires_at ? new Date(sig.expires_at * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false }) : '?';
            return `<span class="signal-chip">
                <span class="dot"></span>
                ${sig.type} armed, trigger &gt; ${Number(sig.high).toFixed(2)}, expires ${expires}
            </span>`;
        }).join('');
    } else {
        chips.innerHTML = '<span class="no-signals">No active signals</span>';
    }
    
    // Update option chart title
    if (currentState.active_trade) {
        const sym = currentState.active_trade.symbol || '';
        document.getElementById('optionChartTitle').textContent = sym.replace('NSE:', '');
    } else {
        const sym = activeOptionType === 'ce' ? (currentState.ce_symbol || 'CE') : (currentState.pe_symbol || 'PE');
        document.getElementById('optionChartTitle').textContent = sym.replace('NSE:', '');
    }
}

// ============ CANDLE POLLING ============

async function pollCandles() {
    if (!currentState) return;
    
    // Spot candles
    const spotData = await fetchJSON(`/api/candles?symbol=${encodeURIComponent('NSE:NIFTY50-INDEX')}`);
    if (spotData && spotSeries) {
        const bars = spotData.map(c => ({
            time: toIST(c.time),
            open: c.open, high: c.high, low: c.low, close: c.close,
        }));
        spotSeries.setData(bars);
    }
    
    // Option candles — use active trade symbol or selected toggle
    let optSymbol;
    if (currentState.active_trade) {
        optSymbol = currentState.active_trade.symbol;
    } else {
        optSymbol = activeOptionType === 'ce' ? currentState.ce_symbol : currentState.pe_symbol;
    }
    
    if (optSymbol) {
        const optData = await fetchJSON(`/api/candles?symbol=${encodeURIComponent(optSymbol)}`);
        if (optData && optionSeries) {
            const bars = optData.map(c => ({
                time: toIST(c.time),
                open: c.open, high: c.high, low: c.low, close: c.close,
            }));
            optionSeries.setData(bars);
            
            // SL/TP lines for active trade
            if (optionSlLine) { try { optionSeries.removePriceLine(optionSlLine); } catch {} }
            if (optionTpLine) { try { optionSeries.removePriceLine(optionTpLine); } catch {} }
            optionSlLine = null;
            optionTpLine = null;
            
            if (currentState.active_trade && currentState.active_trade.symbol === optSymbol) {
                optionSlLine = addPriceLine(optionSeries, currentState.active_trade.sl, '#ef4444', 'SL');
                optionTpLine = addPriceLine(optionSeries, currentState.active_trade.tp, '#22c55e', 'TP');
            }
            
            // Entry/exit markers from today's trades
            await addTradeMarkers(optSymbol, optionSeries);
        }
    }
}

async function addTradeMarkers(symbol, series) {
    const trades = await fetchJSON('/api/trades');
    if (!trades || !series) return;
    
    const markers = [];
    for (const t of trades) {
        if (t.symbol !== symbol) continue;
        
        // Entry marker
        if (t.entry_time) {
            try {
                const entryTs = toIST(Math.floor(new Date(t.entry_time).getTime() / 1000));
                markers.push({
                    time: entryTs,
                    position: 'belowBar',
                    color: '#22c55e',
                    shape: 'arrowUp',
                    text: `ENTRY ${Number(t.entry_price).toFixed(1)}`,
                });
            } catch {}
        }
        
        // Exit marker
        if (t.exit_time) {
            try {
                const exitTs = toIST(Math.floor(new Date(t.exit_time).getTime() / 1000));
                markers.push({
                    time: exitTs,
                    position: 'aboveBar',
                    color: t.exit_reason === 'TP' ? '#22c55e' : '#ef4444',
                    shape: 'arrowDown',
                    text: `${t.exit_reason} ${Number(t.exit_price).toFixed(1)}`,
                });
            } catch {}
        }
    }
    
    setMarkers(series, markers);
}

// ============ TRADE LOG ============

async function loadTrades() {
    const dateFilter = document.getElementById('dateFilter');
    const dateVal = dateFilter.value;
    
    let url = '/api/trades';
    if (dateVal) url += `?date=${dateVal}`;
    
    const trades = await fetchJSON(url);
    const list = document.getElementById('tradeList');
    
    if (!trades || trades.length === 0) {
        list.innerHTML = '<div class="no-trades">No trades yet</div>';
        return;
    }
    
    list.innerHTML = trades.map(t => {
        const entryTime = formatTime(t.entry_time);
        const exitTime = formatTime(t.exit_time);
        const pnl = t.pnl_total || 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
        const typeClass = (t.type || '').toLowerCase().includes('ce') ? 'ce' : 'pe';
        const exitClass = t.exit_reason === 'TP' ? 'exit-tp' : t.exit_reason === 'SL' ? 'exit-sl' : 'exit-eod';
        
        return `<div class="trade-row" onclick="openTradeModal(${t.id})">
            <span class="trade-id">#${t.id}</span>
            <span class="trade-type ${typeClass}">${t.type || '—'}</span>
            <span class="trade-time">${entryTime}→${exitTime}</span>
            <span class="trade-prices">${formatPrice(t.entry_price)}→${formatPrice(t.exit_price)}</span>
            <span class="trade-pnl metric-value ${pnlClass}">${formatINR(pnl)}</span>
            <span class="trade-exit-reason ${exitClass}">${t.exit_reason || '—'}</span>
        </div>`;
    }).join('');
}

async function loadTradeDates() {
    const dates = await fetchJSON('/api/trades/dates');
    const select = document.getElementById('dateFilter');
    
    // Keep the "Today" option
    select.innerHTML = '<option value="">Today</option>';
    if (dates && dates.length > 0) {
        select.innerHTML += '<option value="all">All</option>';
        dates.forEach(d => {
            select.innerHTML += `<option value="${d}">${d}</option>`;
        });
    }
}

// ============ TRADE DETAIL MODAL ============

async function openTradeModal(tradeId) {
    const data = await fetchJSON(`/api/trades/${tradeId}`);
    if (!data || !data.trade) return;
    
    const t = data.trade;
    const modal = document.getElementById('tradeModal');
    const body = document.getElementById('modalBody');
    const title = document.getElementById('modalTitle');
    
    const pnl = t.pnl_total || 0;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';
    title.textContent = `Trade #${t.id} — ${t.type} on ${(t.symbol || '').replace('NSE:', '')}`;
    
    body.innerHTML = `
        <div class="modal-metrics">
            <div class="trade-field">
                <span class="trade-field-label">Entry</span>
                <span class="trade-field-value">${formatPrice(t.entry_price)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Exit</span>
                <span class="trade-field-value">${formatPrice(t.exit_price)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">SL</span>
                <span class="trade-field-value" style="color:var(--red)">${formatPrice(t.sl)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">TP</span>
                <span class="trade-field-value" style="color:var(--green)">${formatPrice(t.tp)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Risk</span>
                <span class="trade-field-value">${formatPrice(t.risk)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Lots</span>
                <span class="trade-field-value">${t.lots}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">P&L</span>
                <span class="trade-field-value ${pnlClass}">${formatINR(pnl)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Exit Reason</span>
                <span class="trade-field-value">${t.exit_reason}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Highest</span>
                <span class="trade-field-value">${formatPrice(t.highest_reached)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Signal</span>
                <span class="trade-field-value" style="font-size:0.8rem">${t.signal_reason || '—'}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Entry Time</span>
                <span class="trade-field-value">${formatTime(t.entry_time)}</span>
            </div>
            <div class="trade-field">
                <span class="trade-field-label">Exit Time</span>
                <span class="trade-field-value">${formatTime(t.exit_time)}</span>
            </div>
        </div>
        <div class="modal-charts">
            <div class="modal-chart-box">
                <div class="modal-chart-label">NIFTY 50 Spot</div>
                <div id="modalSpotChart" style="width:100%;height:calc(100% - 24px)"></div>
            </div>
            <div class="modal-chart-box">
                <div class="modal-chart-label">${(t.symbol || '').replace('NSE:', '')}</div>
                <div id="modalOptionChart" style="width:100%;height:calc(100% - 24px)"></div>
            </div>
        </div>
    `;
    
    modal.classList.add('active');
    
    // Create modal charts
    setTimeout(() => {
        const spotContainer = document.getElementById('modalSpotChart');
        const optContainer = document.getElementById('modalOptionChart');
        
        if (spotContainer) {
            if (modalSpotChart) { modalSpotChart.remove(); modalSpotChart = null; }
            modalSpotChart = LightweightCharts.createChart(spotContainer, {
                width: spotContainer.clientWidth,
                height: spotContainer.clientHeight || 240,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8', fontSize: 10, fontFamily: 'Inter' },
                grid: { vertLines: { color: 'rgba(55,65,81,0.3)' }, horzLines: { color: 'rgba(55,65,81,0.3)' } },
                timeScale: { timeVisible: true, borderColor: 'rgba(55,65,81,0.5)' },
                rightPriceScale: { borderColor: 'rgba(55,65,81,0.5)' },
            });
            const mSpotSeries = modalSpotChart.addCandlestickSeries({
                upColor: '#22c55e', downColor: '#ef4444',
                borderUpColor: '#22c55e', borderDownColor: '#ef4444',
                wickUpColor: '#22c55e', wickDownColor: '#ef4444',
            });
            
            if (data.spot_candles) {
                mSpotSeries.setData(data.spot_candles.map(c => ({
                    time: toIST(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
                })));
            }
            
            // Signal candle marker
            if (t.signal_time) {
                try {
                    const sigTs = toIST(Math.floor(new Date(t.signal_time).getTime() / 1000));
                    mSpotSeries.setMarkers([{
                        time: sigTs, position: 'belowBar', color: '#f59e0b', shape: 'circle', text: 'SIG',
                    }]);
                } catch {}
            }
            
            modalSpotChart.timeScale().fitContent();
        }
        
        if (optContainer) {
            if (modalOptionChart) { modalOptionChart.remove(); modalOptionChart = null; }
            modalOptionChart = LightweightCharts.createChart(optContainer, {
                width: optContainer.clientWidth,
                height: optContainer.clientHeight || 240,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8', fontSize: 10, fontFamily: 'Inter' },
                grid: { vertLines: { color: 'rgba(55,65,81,0.3)' }, horzLines: { color: 'rgba(55,65,81,0.3)' } },
                timeScale: { timeVisible: true, borderColor: 'rgba(55,65,81,0.5)' },
                rightPriceScale: { borderColor: 'rgba(55,65,81,0.5)' },
            });
            const mOptSeries = modalOptionChart.addCandlestickSeries({
                upColor: '#22c55e', downColor: '#ef4444',
                borderUpColor: '#22c55e', borderDownColor: '#ef4444',
                wickUpColor: '#22c55e', wickDownColor: '#ef4444',
            });
            
            if (data.option_candles) {
                mOptSeries.setData(data.option_candles.map(c => ({
                    time: toIST(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
                })));
            }
            
            // SL/TP lines
            addPriceLine(mOptSeries, t.sl, '#ef4444', 'SL');
            addPriceLine(mOptSeries, t.tp, '#22c55e', 'TP');
            
            // Entry/exit markers
            const markers = [];
            if (t.entry_time) {
                try {
                    markers.push({
                        time: toIST(Math.floor(new Date(t.entry_time).getTime() / 1000)),
                        position: 'belowBar', color: '#22c55e', shape: 'arrowUp',
                        text: `ENTRY ${Number(t.entry_price).toFixed(1)}`,
                    });
                } catch {}
            }
            if (t.exit_time) {
                try {
                    markers.push({
                        time: toIST(Math.floor(new Date(t.exit_time).getTime() / 1000)),
                        position: 'aboveBar',
                        color: t.exit_reason === 'TP' ? '#22c55e' : '#ef4444',
                        shape: 'arrowDown',
                        text: `${t.exit_reason} ${Number(t.exit_price).toFixed(1)}`,
                    });
                } catch {}
            }
            if (t.signal_time) {
                try {
                    markers.push({
                        time: toIST(Math.floor(new Date(t.signal_time).getTime() / 1000)),
                        position: 'belowBar', color: '#f59e0b', shape: 'circle', text: 'SIG',
                    });
                } catch {}
            }
            setMarkers(mOptSeries, markers);
            
            modalOptionChart.timeScale().fitContent();
        }
    }, 100);
}

function closeTradeModal() {
    document.getElementById('tradeModal').classList.remove('active');
    if (modalSpotChart) { modalSpotChart.remove(); modalSpotChart = null; }
    if (modalOptionChart) { modalOptionChart.remove(); modalOptionChart = null; }
}

// ============ EVENT FEED ============

async function pollEvents() {
    let url = '/api/events?limit=50';
    if (lastEventId != null) {
        url += `&after_id=${lastEventId}`;
    }
    
    const events = await fetchJSON(url);
    if (!events || events.length === 0) return;
    
    // Track highest event ID
    const maxId = Math.max(...events.map(e => e.id));
    if (lastEventId == null || maxId > lastEventId) {
        lastEventId = maxId;
    }
    
    const feed = document.getElementById('eventFeed');
    
    // If first load, replace placeholder
    if (feed.querySelector('.no-events')) {
        feed.innerHTML = '';
    }
    
    // Prepend new events (they come in DESC order, but we want newest first)
    const newHtml = events.map(e => {
        const kindClass = (e.kind || '').toLowerCase().replace(' ', '_');
        const dataStr = e.data ? ` — ${e.data}` : '';
        return `<div class="event-row">
            <span class="event-time">${formatEventTime(e.ts)}</span>
            <span class="event-kind ${kindClass}">${e.kind}</span>
            <span class="event-message">${e.message || ''}</span>
        </div>`;
    }).join('');
    
    // For first load, just set innerHTML; for incremental, prepend
    if (feed.children.length === 0) {
        feed.innerHTML = newHtml;
    } else {
        // Only prepend truly new events
        feed.insertAdjacentHTML('afterbegin', newHtml);
        
        // Keep max 100 rows
        while (feed.children.length > 100) {
            feed.removeChild(feed.lastChild);
        }
    }
}

// ============ INIT ============

document.addEventListener('DOMContentLoaded', () => {
    // Create main charts
    const spotResult = createChart('spotChartContainer');
    spotChart = spotResult.chart;
    spotSeries = spotResult.series;
    
    const optResult = createChart('optionChartContainer');
    optionChart = optResult.chart;
    optionSeries = optResult.series;
    
    // Sync charts
    setupChartSync(spotChart, optionChart);
    
    // CE/PE toggle
    document.getElementById('btnCE').addEventListener('click', () => {
        activeOptionType = 'ce';
        document.getElementById('btnCE').classList.add('active');
        document.getElementById('btnPE').classList.remove('active');
        pollCandles();
    });
    document.getElementById('btnPE').addEventListener('click', () => {
        activeOptionType = 'pe';
        document.getElementById('btnPE').classList.add('active');
        document.getElementById('btnCE').classList.remove('active');
        pollCandles();
    });
    
    // Date filter
    document.getElementById('dateFilter').addEventListener('change', loadTrades);
    
    // Modal close
    document.getElementById('modalClose').addEventListener('click', closeTradeModal);
    document.getElementById('tradeModal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) closeTradeModal();
    });
    
    // Keyboard close
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeTradeModal();
    });
    
    // Initial loads
    pollState();
    pollCandles();
    loadTrades();
    loadTradeDates();
    pollEvents();
    
    // Polling intervals
    setInterval(pollState, POLL_STATE_MS);
    setInterval(pollCandles, POLL_CANDLE_MS);
    setInterval(pollEvents, POLL_EVENTS_MS);
    setInterval(loadTrades, 10000);
    setInterval(loadTradeDates, 30000);
});
