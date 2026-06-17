/**
 * Live Trading Dashboard — Frontend
 * ===================================
 * Poll-based architecture (no WebSocket needed).
 * Uses lightweight-charts v4.2.3 (v4 API: addCandlestickSeries).
 *
 * Header behaviour:
 *   Capital  → always from /api/state  (final account balance, never date-filtered)
 *   Spot     → always from /api/state  (current/last known LTP, never date-filtered)
 *   Day P&L  → from /api/summary?date= (changes with date selector)
 *   Signals  → from /api/summary?date= (changes with date selector)
 *
 * Default selected date = most recent date with trades
 * (on a live day that is today, so live mode works identically).
 */

// ============ CONSTANTS ============

const IST_OFFSET = 19800; // +5:30 in seconds
const POLL_STATE_MS   = 2500;
const POLL_CANDLE_MS  = 5000;
const POLL_EVENTS_MS  = 3000;
const POLL_SUMMARY_MS = 5000;

// ============ STATE ============

let spotChart    = null;
let spotSeries   = null;
let optionChart  = null;
let optionSeries = null;
let optionSlLine = null;
let optionTpLine = null;
let optionEntryLine = null;
let activeOptionType = 'ce';   // legacy fallback ('ce' or 'pe')
let selectedOptionSymbol = '';  // explicit option contract chosen in the dropdown
let availableOptionSymbols = []; // [{symbol,type,count,from,to}] for the selected date
let currentState     = null;
let lastEventId      = null;
let syncingCharts    = false;
let modalSpotChart   = null;
let modalOptionChart = null;

// The date currently shown in the trade journal / charts / summary.
// Kept in sync with the <select> value.
let selectedDate = '';   // '' means "use server default (most recent)"

// Option symbols (CE/PE) actually traded on the currently-selected date.
// Used so the option chart shows the right contract when browsing a PAST date,
// where state.ce_symbol/pe_symbol (the latest day's contracts) would not match.
let selectedDayOptionSymbols = { ce: null, pe: null };

// Guards the one-time full refresh triggered from loadTradeDates().
let dateDropdownInitialized = false;

// ============ HELPERS ============

function toIST(utcEpoch) {
    return utcEpoch + IST_OFFSET;
}

function toBarTime(utcEpoch) {
    return toIST(Math.floor(utcEpoch / 300) * 300);
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
    } catch { return isoStr; }
}

function formatEventTime(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch { return ''; }
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

// ============ CHART CREATION ============

function createChart(containerId, height) {
    const container = document.getElementById(containerId);
    if (!container) return { chart: null, series: null };
    container.innerHTML = '';

    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: height || container.clientHeight || 300,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#94a3b8', fontSize: 11, fontFamily: 'Inter, sans-serif',
        },
        grid: {
            vertLines: { color: 'rgba(55, 65, 81, 0.3)' },
            horzLines: { color: 'rgba(55, 65, 81, 0.3)' },
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(55, 65, 81, 0.5)' },
        timeScale: { borderColor: 'rgba(55, 65, 81, 0.5)', timeVisible: true, secondsVisible: false },
    });

    const series = chart.addCandlestickSeries({
        upColor: '#22c55e', downColor: '#ef4444',
        borderUpColor: '#22c55e', borderDownColor: '#ef4444',
        wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    });

    const ro = new ResizeObserver(entries => {
        for (const e of entries) {
            chart.applyOptions({ width: e.contentRect.width, height: e.contentRect.height });
        }
    });
    ro.observe(container);

    return { chart, series };
}

function addPriceLine(series, price, color, title, lineStyle) {
    if (!series || price == null) return null;
    return series.createPriceLine({
        price, color, lineWidth: 1,
        lineStyle: lineStyle || LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true, title: title || '',
    });
}

function setMarkers(series, markers) {
    if (!series) return;
    try { series.setMarkers(markers.sort((a, b) => a.time - b.time)); }
    catch (e) { console.error('setMarkers error:', e); }
}

// ============ CHART SYNC ============

function setupChartSync(c1, c2) {
    if (!c1 || !c2) return;
    c1.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (syncingCharts || !r) return;
        syncingCharts = true; c2.timeScale().setVisibleLogicalRange(r); syncingCharts = false;
    });
    c2.timeScale().subscribeVisibleLogicalRangeChange(r => {
        if (syncingCharts || !r) return;
        syncingCharts = true; c1.timeScale().setVisibleLogicalRange(r); syncingCharts = false;
    });
}

// ============ STATE POLLING (Capital + Spot only) ============

async function pollState() {
    const data = await fetchJSON('/api/state');
    if (!data) return;

    currentState = data.state;
    const live = data.live;

    // Live indicator
    const dot   = document.getElementById('liveDot');
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

    // ── Capital — always the current account balance, never date-filtered ──
    document.getElementById('capitalValue').textContent =
        formatPrice(currentState.running_capital);

    // ── Spot — always the latest known price, never date-filtered ──
    document.getElementById('spotValue').textContent =
        currentState.spot_ltp != null ? Number(currentState.spot_ltp).toFixed(2) : '—';

    // Day P&L and Signals are intentionally NOT updated here.
    // They are driven by loadDaySummary() so they track the selected date.

    // Active trade card
    const tradeCard = document.getElementById('activeTradeCard');
    const tradeGrid = document.getElementById('activeTradeGrid');
    const unrealPnl = document.getElementById('tradeUnrealizedPnl');

    if (currentState.active_trade) {
        tradeCard.style.display = 'block';
        const t = currentState.active_trade;
        const optLtp = t.type === 'CE_BUY' ? currentState.ce_ltp : currentState.pe_ltp;

        if (optLtp != null) {
            const uPnl = (optLtp - t.entry) * 65 * (t.lots || 1);
            unrealPnl.textContent = formatINR(uPnl);
            unrealPnl.className = 'metric-value ' + (uPnl >= 0 ? 'positive' : 'negative');
        }

        let timeInTrade = '—';
        if (t.entry_time) {
            const elapsed = Math.floor((Date.now() - new Date(t.entry_time).getTime()) / 1000);
            timeInTrade = `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
        }

        tradeGrid.innerHTML = `
            <div class="trade-field"><span class="trade-field-label">Type</span><span class="trade-field-value">${t.type || '—'}</span></div>
            <div class="trade-field"><span class="trade-field-label">Symbol</span><span class="trade-field-value" style="font-size:0.75rem">${(t.symbol || '—').replace('NSE:', '')}</span></div>
            <div class="trade-field"><span class="trade-field-label">Entry</span><span class="trade-field-value">${formatPrice(t.entry)}</span></div>
            <div class="trade-field"><span class="trade-field-label">SL</span><span class="trade-field-value" style="color:var(--red)">${formatPrice(t.sl)}</span></div>
            <div class="trade-field"><span class="trade-field-label">TP</span><span class="trade-field-value" style="color:var(--green)">${formatPrice(t.tp)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Lots</span><span class="trade-field-value">${t.lots || '—'}</span></div>
            <div class="trade-field"><span class="trade-field-label">LTP</span><span class="trade-field-value">${formatPrice(optLtp)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Time</span><span class="trade-field-value">${timeInTrade}</span></div>`;
    } else {
        tradeCard.style.display = 'none';
    }

    // Pending signals
    const chips = document.getElementById('signalChips');
    if (currentState.pending_signals && currentState.pending_signals.length > 0) {
        chips.innerHTML = currentState.pending_signals.map(sig => {
            const expires = sig.expires_at
                ? new Date(sig.expires_at * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })
                : '?';
            return `<span class="signal-chip"><span class="dot"></span>${sig.type} armed, trigger &gt; ${Number(sig.high).toFixed(2)}, expires ${expires}</span>`;
        }).join('');
    } else {
        chips.innerHTML = '<span class="no-signals">No active signals</span>';
    }

    // Option chart title is managed by pollCandles (follows the selected
    // contract / dropdown). When an active trade appears, auto-follow it.
    if (currentState.active_trade && currentState.active_trade.symbol) {
        document.getElementById('optionChartTitle').textContent =
            currentState.active_trade.symbol.replace('NSE:', '');
    }
}

// ============ DAY SUMMARY (Day P&L + Signals — date-aware) ============

async function loadDaySummary(date) {
    const url = date ? `/api/summary?date=${date}` : '/api/summary';
    const data = await fetchJSON(url);
    if (!data) return;

    const pnlEl = document.getElementById('pnlValue');
    const pnlVal = data.daily_pnl || 0;
    pnlEl.textContent = formatINR(pnlVal);
    pnlEl.className = 'metric-value ' + (pnlVal > 0 ? 'positive' : pnlVal < 0 ? 'negative' : 'neutral');

    document.getElementById('signalsValue').textContent = data.signal_count || 0;
}

// ============ OPTION SYMBOL DROPDOWN (dynamic, rotation-aware) ============

async function loadOptionSymbols() {
    const url = (selectedDate && selectedDate !== 'all')
        ? `/api/symbols?date=${selectedDate}` : '/api/symbols';
    const syms = await fetchJSON(url);
    availableOptionSymbols = Array.isArray(syms) ? syms : [];

    const sel = document.getElementById('optionSelect');
    if (!sel) return;

    const prev = selectedOptionSymbol || sel.value;

    if (availableOptionSymbols.length === 0) {
        sel.innerHTML = '<option value="">—</option>';
        selectedOptionSymbol = '';
        return;
    }

    sel.innerHTML = availableOptionSymbols.map(s => {
        const label = `${s.symbol.replace('NSE:', '')} (${s.type})`;
        return `<option value="${s.symbol}">${label}</option>`;
    }).join('');

    // Preserve prior selection if still present; else default to the active
    // trade's contract, else the first contract of the day.
    const symList = availableOptionSymbols.map(s => s.symbol);
    let pick = '';
    if (prev && symList.includes(prev)) {
        pick = prev;
    } else if (currentState && currentState.active_trade &&
               symList.includes(currentState.active_trade.symbol)) {
        pick = currentState.active_trade.symbol;
    } else {
        pick = symList[0];
    }
    sel.value = pick;
    selectedOptionSymbol = pick;
}

// ============ CANDLE POLLING ============

async function pollCandles() {
    if (!currentState) return;

    const dateParam = selectedDate ? `&date=${selectedDate}` : '';

    // Spot candles
    const spotData = await fetchJSON(
        `/api/candles?symbol=${encodeURIComponent('NSE:NIFTY50-INDEX')}${dateParam}`
    );
    if (spotData && spotSeries) {
        spotSeries.setData(spotData.map(c => ({
            time: toIST(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
        })));
    }

    // Option candles — the dropdown (#optionSelect) is authoritative when the
    // user has picked a contract. Otherwise auto-follow the active trade, then
    // fall back to the first available contract for the date.
    let optSymbol = selectedOptionSymbol;
    if (!optSymbol) {
        if (currentState.active_trade) {
            optSymbol = currentState.active_trade.symbol;
        } else if (availableOptionSymbols.length > 0) {
            optSymbol = availableOptionSymbols[0].symbol;
        } else if (selectedDate && selectedDate !== 'all' &&
                   (selectedDayOptionSymbols.ce || selectedDayOptionSymbols.pe)) {
            optSymbol = selectedDayOptionSymbols.ce || selectedDayOptionSymbols.pe;
        } else {
            optSymbol = currentState.ce_symbol || currentState.pe_symbol;
        }
    }

    // Reflect the contract being shown in the chart title.
    const titleEl = document.getElementById('optionChartTitle');
    if (titleEl && optSymbol) titleEl.textContent = optSymbol.replace('NSE:', '');

    if (optSymbol) {
        const optData = await fetchJSON(
            `/api/candles?symbol=${encodeURIComponent(optSymbol)}${dateParam}`
        );
        if (optData && optionSeries) {
            optionSeries.setData(optData.map(c => ({
                time: toIST(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
            })));

            // Remove old SL/TP/Entry lines
            if (optionSlLine) { try { optionSeries.removePriceLine(optionSlLine); } catch {} }
            if (optionTpLine) { try { optionSeries.removePriceLine(optionTpLine); } catch {} }
            if (optionEntryLine) { try { optionSeries.removePriceLine(optionEntryLine); } catch {} }
            optionSlLine = null; optionTpLine = null; optionEntryLine = null;

            if (currentState.active_trade && currentState.active_trade.symbol === optSymbol) {
                optionSlLine = addPriceLine(optionSeries, currentState.active_trade.sl, '#ef4444', 'SL');
                optionTpLine = addPriceLine(optionSeries, currentState.active_trade.tp, '#22c55e', 'TP');
                optionEntryLine = addPriceLine(optionSeries, currentState.active_trade.entry, '#3b82f6', 'ENTRY',
                    LightweightCharts.LineStyle.Solid);
            }

            await addTradeMarkers(optSymbol, optionSeries);
        }
    } else if (optionSeries) {
        // No contract for this date/toggle — clear the option chart.
        optionSeries.setData([]);
        if (optionSlLine) { try { optionSeries.removePriceLine(optionSlLine); } catch {} optionSlLine = null; }
        if (optionTpLine) { try { optionSeries.removePriceLine(optionTpLine); } catch {} optionTpLine = null; }
        if (optionEntryLine) { try { optionSeries.removePriceLine(optionEntryLine); } catch {} optionEntryLine = null; }
        try { optionSeries.setMarkers([]); } catch {}
    }
}

async function addTradeMarkers(symbol, series) {
    const dateParam = selectedDate ? `?date=${selectedDate}` : '';
    const trades = await fetchJSON(`/api/trades${dateParam}`);
    if (!trades || !series) return;

    const markers = [];
    for (const t of trades) {
        if (t.symbol !== symbol) continue;
        if (t.entry_time) {
            try {
                markers.push({
                    time: toBarTime(new Date(t.entry_time).getTime() / 1000),
                    position: 'belowBar', color: '#22c55e', shape: 'arrowUp',
                    text: `ENTRY ${Number(t.entry_price).toFixed(1)}`,
                });
            } catch {}
        }
        if (t.exit_time) {
            try {
                markers.push({
                    time: toBarTime(new Date(t.exit_time).getTime() / 1000),
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
    const dateVal = document.getElementById('dateFilter').value;
    selectedDate = dateVal;   // keep module-level state in sync

    const url = dateVal ? `/api/trades?date=${dateVal}` : '/api/trades';
    const trades = await fetchJSON(url);
    const list = document.getElementById('tradeList');

    if (!trades || trades.length === 0) {
        selectedDayOptionSymbols = { ce: null, pe: null };
        list.innerHTML = '<div class="no-trades">No trades yet</div>';
        return;
    }

    // Capture the CE/PE contracts traded on this date so the option chart can
    // render them when browsing history (most recent trade of each type wins).
    const ceSyms = { ce: null, pe: null };
    for (const t of trades) {
        const isCe = (t.type || '').toUpperCase().includes('CE');
        if (isCe) ceSyms.ce = t.symbol;
        else ceSyms.pe = t.symbol;
    }
    selectedDayOptionSymbols = ceSyms;

    list.innerHTML = trades.map(t => {
        const pnl = t.pnl_total || 0;
        return `<div class="trade-row" onclick="openTradeModal(${t.id})">
            <span class="trade-id">#${t.id}</span>
            <span class="trade-type ${(t.type||'').toLowerCase().includes('ce') ? 'ce' : 'pe'}">${t.type || '—'}</span>
            <span class="trade-time">${formatTime(t.entry_time)}→${formatTime(t.exit_time)}</span>
            <span class="trade-prices">${formatPrice(t.entry_price)}→${formatPrice(t.exit_price)}</span>
            <span class="trade-pnl metric-value ${pnl >= 0 ? 'positive' : 'negative'}">${formatINR(pnl)}</span>
            <span class="trade-exit-reason ${t.exit_reason === 'TP' ? 'exit-tp' : t.exit_reason === 'SL' ? 'exit-sl' : 'exit-eod'}">${t.exit_reason || '—'}</span>
        </div>`;
    }).join('');
}

async function loadTradeDates() {
    const dates = await fetchJSON('/api/trades/dates');
    const select = document.getElementById('dateFilter');

    // Preserve whatever the user currently has selected so the periodic refresh
    // (every 30s) doesn't clobber their chosen date.
    const prev = select.value;

    select.innerHTML = '<option value="">Today</option>';
    if (dates && dates.length > 0) {
        select.innerHTML += '<option value="all">All</option>';
        dates.forEach(d => {
            select.innerHTML += `<option value="${d}">${d}</option>`;
        });

        const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD local

        // Restore prior selection if it still exists in the list.
        if (prev && (prev === 'all' || prev === '' || dates.includes(prev))) {
            select.value = prev;
            selectedDate = prev;
        } else if (!dates.includes(today)) {
            // First load with no live data for today → default to most recent.
            select.value = dates[0]; // dates are DESC, so [0] is most recent
            selectedDate = dates[0];
        }
    } else if (prev) {
        // No dates returned but keep prior selection value if any.
        select.value = prev === 'all' ? '' : prev;
    }

    // Only do a full date-dependent refresh on the FIRST load. On the periodic
    // 30s refresh the selection is unchanged, so we skip the reload to avoid
    // fighting the user / re-fetching needlessly.
    if (!dateDropdownInitialized) {
        dateDropdownInitialized = true;
        await loadTrades();
        await loadDaySummary(selectedDate);
        await pollCandles();
    }
}

// ============ TRADE DETAIL MODAL ============

async function openTradeModal(tradeId) {
    const data = await fetchJSON(`/api/trades/${tradeId}`);
    if (!data || !data.trade) return;

    const t = data.trade;
    const modal  = document.getElementById('tradeModal');
    const body   = document.getElementById('modalBody');
    const title  = document.getElementById('modalTitle');
    const pnl    = t.pnl_total || 0;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';

    title.textContent = `Trade #${t.id} — ${t.type} on ${(t.symbol || '').replace('NSE:', '')}`;
    body.innerHTML = `
        <div class="modal-metrics">
            <div class="trade-field"><span class="trade-field-label">Entry</span><span class="trade-field-value">${formatPrice(t.entry_price)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Exit</span><span class="trade-field-value">${formatPrice(t.exit_price)}</span></div>
            <div class="trade-field"><span class="trade-field-label">SL</span><span class="trade-field-value" style="color:var(--red)">${formatPrice(t.sl)}</span></div>
            <div class="trade-field"><span class="trade-field-label">TP</span><span class="trade-field-value" style="color:var(--green)">${formatPrice(t.tp)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Risk</span><span class="trade-field-value">${formatPrice(t.risk)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Lots</span><span class="trade-field-value">${t.lots}</span></div>
            <div class="trade-field"><span class="trade-field-label">P&L</span><span class="trade-field-value ${pnlClass}">${formatINR(pnl)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Exit Reason</span><span class="trade-field-value">${t.exit_reason}</span></div>
            <div class="trade-field"><span class="trade-field-label">Highest</span><span class="trade-field-value">${formatPrice(t.highest_reached)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Signal</span><span class="trade-field-value" style="font-size:0.8rem">${t.signal_reason || '—'}</span></div>
            <div class="trade-field"><span class="trade-field-label">Entry Time</span><span class="trade-field-value">${formatTime(t.entry_time)}</span></div>
            <div class="trade-field"><span class="trade-field-label">Exit Time</span><span class="trade-field-value">${formatTime(t.exit_time)}</span></div>
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
        </div>`;

    modal.classList.add('active');

    setTimeout(() => {
        // Spot modal chart
        const spotCont = document.getElementById('modalSpotChart');
        if (spotCont) {
            if (modalSpotChart) { modalSpotChart.remove(); modalSpotChart = null; }
            modalSpotChart = LightweightCharts.createChart(spotCont, {
                width: spotCont.clientWidth, height: spotCont.clientHeight || 240,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8', fontSize: 10, fontFamily: 'Inter' },
                grid: { vertLines: { color: 'rgba(55,65,81,0.3)' }, horzLines: { color: 'rgba(55,65,81,0.3)' } },
                timeScale: { timeVisible: true, borderColor: 'rgba(55,65,81,0.5)' },
                rightPriceScale: { borderColor: 'rgba(55,65,81,0.5)' },
            });
            const mSpot = modalSpotChart.addCandlestickSeries({
                upColor:'#22c55e',downColor:'#ef4444',borderUpColor:'#22c55e',borderDownColor:'#ef4444',wickUpColor:'#22c55e',wickDownColor:'#ef4444',
            });
            if (data.spot_candles) {
                mSpot.setData(data.spot_candles.map(c => ({ time: toIST(c.time), open:c.open, high:c.high, low:c.low, close:c.close })));
            }
            if (t.signal_time) {
                try {
                    mSpot.setMarkers([{ time: toBarTime(new Date(t.signal_time).getTime()/1000), position:'belowBar', color:'#f59e0b', shape:'circle', text:'SIG' }]);
                } catch {}
            }
            modalSpotChart.timeScale().fitContent();
        }

        // Option modal chart
        const optCont = document.getElementById('modalOptionChart');
        if (optCont) {
            if (modalOptionChart) { modalOptionChart.remove(); modalOptionChart = null; }
            modalOptionChart = LightweightCharts.createChart(optCont, {
                width: optCont.clientWidth, height: optCont.clientHeight || 240,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8', fontSize: 10, fontFamily: 'Inter' },
                grid: { vertLines: { color: 'rgba(55,65,81,0.3)' }, horzLines: { color: 'rgba(55,65,81,0.3)' } },
                timeScale: { timeVisible: true, borderColor: 'rgba(55,65,81,0.5)' },
                rightPriceScale: { borderColor: 'rgba(55,65,81,0.5)' },
            });
            const mOpt = modalOptionChart.addCandlestickSeries({
                upColor:'#22c55e',downColor:'#ef4444',borderUpColor:'#22c55e',borderDownColor:'#ef4444',wickUpColor:'#22c55e',wickDownColor:'#ef4444',
            });
            if (data.option_candles) {
                mOpt.setData(data.option_candles.map(c => ({ time: toIST(c.time), open:c.open, high:c.high, low:c.low, close:c.close })));
            }
            addPriceLine(mOpt, t.sl, '#ef4444', 'SL');
            addPriceLine(mOpt, t.tp, '#22c55e', 'TP');
            addPriceLine(mOpt, t.entry_price, '#3b82f6', 'ENTRY', LightweightCharts.LineStyle.Solid);

            const markers = [];
            if (t.entry_time) { try { markers.push({ time: toBarTime(new Date(t.entry_time).getTime()/1000), position:'belowBar', color:'#22c55e', shape:'arrowUp', text:`ENTRY ${Number(t.entry_price).toFixed(1)}` }); } catch {} }
            if (t.exit_time)  { try { markers.push({ time: toBarTime(new Date(t.exit_time).getTime()/1000),  position:'aboveBar', color: t.exit_reason==='TP'?'#22c55e':'#ef4444', shape:'arrowDown', text:`${t.exit_reason} ${Number(t.exit_price).toFixed(1)}` }); } catch {} }
            if (t.signal_time){ try { markers.push({ time: toBarTime(new Date(t.signal_time).getTime()/1000), position:'belowBar', color:'#f59e0b', shape:'circle', text:'SIG' }); } catch {} }
            setMarkers(mOpt, markers);

            modalOptionChart.timeScale().fitContent();
        }
    }, 100);
}

function closeTradeModal() {
    document.getElementById('tradeModal').classList.remove('active');
    if (modalSpotChart)   { modalSpotChart.remove();   modalSpotChart = null; }
    if (modalOptionChart) { modalOptionChart.remove(); modalOptionChart = null; }
}

// ============ EVENT FEED ============

async function pollEvents() {
    let url = '/api/events?limit=50';
    if (lastEventId != null) url += `&after_id=${lastEventId}`;

    const events = await fetchJSON(url);
    if (!events || events.length === 0) return;

    const maxId = Math.max(...events.map(e => e.id));
    if (lastEventId == null || maxId > lastEventId) lastEventId = maxId;

    const feed = document.getElementById('eventFeed');
    if (feed.querySelector('.no-events')) feed.innerHTML = '';

    const newHtml = events.map(e => {
        const kindClass = (e.kind || '').toLowerCase().replace(' ', '_');
        return `<div class="event-row">
            <span class="event-time">${formatEventTime(e.ts)}</span>
            <span class="event-kind ${kindClass}">${e.kind}</span>
            <span class="event-message">${e.message || ''}</span>
        </div>`;
    }).join('');

    if (feed.children.length === 0) {
        feed.innerHTML = newHtml;
    } else {
        feed.insertAdjacentHTML('afterbegin', newHtml);
        while (feed.children.length > 100) feed.removeChild(feed.lastChild);
    }
}

// ============ DATE FILTER CHANGE ============

async function onDateChange() {
    const dateVal = document.getElementById('dateFilter').value;
    selectedDate = dateVal;
    // Reset the contract selection so the dropdown re-populates for the new date.
    selectedOptionSymbol = '';
    // Load trades first so selectedDayOptionSymbols is populated before the
    // option chart is redrawn for the newly selected date.
    await loadTrades();
    await loadOptionSymbols();
    loadDaySummary(selectedDate);
    pollCandles();
}

// ============ INIT ============

document.addEventListener('DOMContentLoaded', () => {
    // Create main charts
    ({ chart: spotChart, series: spotSeries } = createChart('spotChartContainer'));
    ({ chart: optionChart, series: optionSeries } = createChart('optionChartContainer'));
    setupChartSync(spotChart, optionChart);

    // Option contract dropdown — user pick is authoritative.
    document.getElementById('optionSelect').addEventListener('change', (e) => {
        selectedOptionSymbol = e.target.value || '';
        pollCandles();
    });

    // Date filter — single handler
    document.getElementById('dateFilter').addEventListener('change', onDateChange);

    // Modal close
    document.getElementById('modalClose').addEventListener('click', closeTradeModal);
    document.getElementById('tradeModal').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeTradeModal();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTradeModal(); });

    // Initial load order:
    // 1. State (gives Capital + Spot immediately)
    // 2. Trade dates → sets dropdown → triggers loadTrades + loadDaySummary + pollCandles
    // 3. Option symbols → populate the contract dropdown
    // 4. Events
    pollState().then(() => {
        loadTradeDates();   // async: sets dropdown, then loads everything date-dependent
        loadOptionSymbols().then(pollCandles);
        pollEvents();
    });

    // Polling intervals
    setInterval(pollState,        POLL_STATE_MS);
    setInterval(pollCandles,      POLL_CANDLE_MS);
    setInterval(pollEvents,       POLL_EVENTS_MS);
    setInterval(() => loadDaySummary(selectedDate), POLL_SUMMARY_MS);
    setInterval(loadTrades,       10000);
    setInterval(loadTradeDates,   30000);
    setInterval(loadOptionSymbols, 15000);  // refresh contract list (rotation adds new ones)
});
