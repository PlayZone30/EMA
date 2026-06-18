/**
 * Live Trading Dashboard — Frontend
 * ===================================
 * Poll-based architecture (no WebSocket needed).
 * Uses lightweight-charts v4.2.3 (v4 API: addCandlestickSeries).
 *
 * Fixed-universe model: the engine subscribes ~60 strikes and no longer
 * persists live tick candles. There is no single "current" CE/PE contract, so
 * the live spot/option chart panels were removed. Charts are shown ONLY in the
 * trade-detail modal, backfilled from the History API when a trade executes.
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
const POLL_EVENTS_MS  = 3000;
const POLL_SUMMARY_MS = 5000;

// ============ STATE ============

let currentState     = null;
let lastEventId      = null;
let modalSpotChart   = null;
let modalOptionChart = null;

// The date currently shown in the trade journal / summary.
// Kept in sync with the <select> value. '' means "use server default".
let selectedDate = '';

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

// ============ STATE POLLING (Capital + Spot + active trade + pending) ============

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

    // Day P&L and Signals are driven by loadDaySummary() (date-aware).

    // Active trade card
    const tradeCard = document.getElementById('activeTradeCard');
    const tradeGrid = document.getElementById('activeTradeGrid');
    const unrealPnl = document.getElementById('tradeUnrealizedPnl');

    if (currentState.active_trade) {
        tradeCard.style.display = 'block';
        const t = currentState.active_trade;
        // Fixed-universe model: the traded contract's current price is carried
        // on the active_trade blob itself (state.ltps no longer exposes a single CE/PE).
        const optLtp = t.ltp;

        if (optLtp != null) {
            const uPnl = (optLtp - t.entry) * 65 * (t.lots || 1);
            unrealPnl.textContent = formatINR(uPnl);
            unrealPnl.className = 'metric-value ' + (uPnl >= 0 ? 'positive' : 'negative');
        } else {
            unrealPnl.textContent = '—';
            unrealPnl.className = 'metric-value neutral';
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

// ============ TRADE LOG ============

async function loadTrades() {
    const dateVal = document.getElementById('dateFilter').value;
    selectedDate = dateVal;   // keep module-level state in sync

    const url = dateVal ? `/api/trades?date=${dateVal}` : '/api/trades';
    const trades = await fetchJSON(url);
    const list = document.getElementById('tradeList');

    if (!trades || trades.length === 0) {
        list.innerHTML = '<div class="no-trades">No trades yet</div>';
        return;
    }

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
    // doesn't clobber their chosen date.
    const prev = select.value;

    select.innerHTML = '<option value="">Today</option>';
    if (dates && dates.length > 0) {
        select.innerHTML += '<option value="all">All</option>';
        dates.forEach(d => {
            select.innerHTML += `<option value="${d}">${d}</option>`;
        });

        const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD local

        if (prev && (prev === 'all' || prev === '' || dates.includes(prev))) {
            select.value = prev;
            selectedDate = prev;
        } else if (!dates.includes(today)) {
            select.value = dates[0]; // dates are DESC, so [0] is most recent
            selectedDate = dates[0];
        }
    } else if (prev) {
        select.value = prev === 'all' ? '' : prev;
    }

    // Only do a full date-dependent refresh on the FIRST load.
    if (!dateDropdownInitialized) {
        dateDropdownInitialized = true;
        await loadTrades();
        await loadDaySummary(selectedDate);
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
    await loadTrades();
    loadDaySummary(selectedDate);
}

// ============ INIT ============

document.addEventListener('DOMContentLoaded', () => {
    // Date filter — single handler
    document.getElementById('dateFilter').addEventListener('change', onDateChange);

    // Modal close
    document.getElementById('modalClose').addEventListener('click', closeTradeModal);
    document.getElementById('tradeModal').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeTradeModal();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeTradeModal(); });

    // Initial load order:
    // 1. State (gives Capital + Spot + active trade immediately)
    // 2. Trade dates → sets dropdown → triggers loadTrades + loadDaySummary
    // 3. Events
    pollState().then(() => {
        loadTradeDates();
        pollEvents();
    });

    // Polling intervals
    setInterval(pollState,  POLL_STATE_MS);
    setInterval(pollEvents, POLL_EVENTS_MS);
    setInterval(() => loadDaySummary(selectedDate), POLL_SUMMARY_MS);
    setInterval(loadTrades, 10000);
    setInterval(loadTradeDates, 30000);
});

// Expose modal opener for inline onclick handlers in the trade list.
window.openTradeModal = openTradeModal;
