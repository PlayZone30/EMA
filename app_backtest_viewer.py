import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

st.set_page_config(layout="wide", page_title="Divergence Strategy Backtest Viewer")

BASE_OPTIONS_DIR = "/Users/pavanreddy/EMA/2024_daywise"
BASE_SPOT_DIR = "/Users/pavanreddy/EMA/2024_daywise_spot"
RESULTS_CSV = "/Users/pavanreddy/EMA/backtest_2024_results.csv"

@st.cache_data
def load_results():
    if not os.path.exists(RESULTS_CSV):
        return pd.DataFrame()
    df = pd.read_csv(RESULTS_CSV)
    # create a unique Trade ID
    df['Trade_ID'] = df.index + 1
    return df

@st.cache_data
def load_day_data(date_str, symbol):
    # Load Spot
    spot_file = os.path.join(BASE_SPOT_DIR, f"nifty_spot_5min_{date_str}.csv")
    spot_df = pd.read_csv(spot_file) if os.path.exists(spot_file) else None
    
    # Load Option
    opt_df = None
    try:
        # Assuming folder format is DDMMMYY like 01APR24
        # We need to map YYYY-MM-DD back to DDMMMYY
        # E.g. 2024-04-01 -> 01APR24
        dt_obj = pd.to_datetime(date_str)
        folder_name = dt_obj.strftime("%d%b%y").upper()
        folder_path = os.path.join(BASE_OPTIONS_DIR, folder_name)
        opt_file = os.path.join(folder_path, f"{symbol}.csv")
        if os.path.exists(opt_file):
            opt_df = pd.read_csv(opt_file)
    except Exception as e:
        st.error(f"Error finding option data folder for {date_str}: {e}")
        
    return spot_df, opt_df

def plot_candlestick(df, title, signal_time=None, entry_time=None, exit_time=None, 
                     entry_price=None, sl=None, tp=None, is_option=False):
    fig = go.Figure()

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df['datetime'],
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name='Candles',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350'
    ))

    # Highlight Signal Time
    if signal_time and signal_time in df['datetime'].values:
        fig.add_vline(x=signal_time, line_width=2, line_dash="dash", line_color="blue")
        # Add dummy trace for legend
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=dict(color="blue", width=2, dash="dash"), name="Signal Time"))
        
    if is_option:
        # Entry Time
        if entry_time and entry_time in df['datetime'].values:
            fig.add_vline(x=entry_time, line_width=2, line_dash="dot", line_color="purple")
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=dict(color="purple", width=2, dash="dot"), name="Entry Time"))
        
        # Exit Time
        if exit_time and exit_time in df['datetime'].values:
            fig.add_vline(x=exit_time, line_width=2, line_dash="dash", line_color="black")
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=dict(color="black", width=2, dash="dash"), name="Exit Time"))

        # Horizontal Lines for Entry, SL, TP
        if entry_price:
            fig.add_trace(go.Scatter(
                x=[df['datetime'].iloc[0], df['datetime'].iloc[-1]],
                y=[entry_price, entry_price],
                mode="lines", line=dict(color="blue", width=2, dash="dash"), name="Entry"
            ))
        if sl:
            fig.add_trace(go.Scatter(
                x=[df['datetime'].iloc[0], df['datetime'].iloc[-1]],
                y=[sl, sl],
                mode="lines", line=dict(color="red", width=2, dash="dash"), name="SL"
            ))
        if tp:
            fig.add_trace(go.Scatter(
                x=[df['datetime'].iloc[0], df['datetime'].iloc[-1]],
                y=[tp, tp],
                mode="lines", line=dict(color="green", width=2, dash="dash"), name="TP"
            ))

    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        height=500,
        margin=dict(l=20, r=20, t=40, b=20),
        plot_bgcolor="white",
        paper_bgcolor="white"
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
    return fig

def main():
    st.title("📈 Divergence Backtest Visualization (2024)")
    
    results = load_results()
    if results.empty:
        st.error("Results CSV not found. Run the backtest first!")
        return

    # Sidebar Filters
    st.sidebar.header("Filters")
    case_filter = st.sidebar.multiselect("Select Case", options=results['Case'].unique(), default=results['Case'].unique())
    outcome_filter = st.sidebar.multiselect("Select Outcome", options=results['Outcome'].unique(), default=results['Outcome'].unique())
    
    date_options = ["All"] + list(results['Date'].unique())
    date_filter = st.sidebar.selectbox("Filter by Date", options=date_options)
    
    # Apply Filters
    filtered_df = results[
        (results['Case'].isin(case_filter)) &
        (results['Outcome'].isin(outcome_filter))
    ]
    if date_filter != "All":
        filtered_df = filtered_df[filtered_df['Date'] == date_filter]
        
    st.sidebar.write(f"**Showing {len(filtered_df)} trades**")

    if filtered_df.empty:
        st.warning("No trades match your filters.")
        return

    tab1, tab2 = st.tabs(["📊 Trade Inspector", "📈 Equity Simulator"])

    with tab2:
        st.subheader("Account Compounding Simulator")
        colA, colB = st.columns(2)
        start_cap = colA.number_input("Starting Capital (₹)", value=20000, step=1000)
        lot_size = colB.number_input("Options Lot Size (Qty)", value=65, step=1)
        sim_mode = st.radio("Simulation Mode", ["Fixed 1 Lot", "Compounding (Max Lots)", "Fixed 1 Lot (Stop when bankrupt)"], horizontal=True)
        
        sim_df = filtered_df.copy()
        
        # Ensure chronological plotting by combining Date and Time for a unique X coordinate
        sim_df['DateTime'] = pd.to_datetime(sim_df['Date'] + ' ' + sim_df['Entry_Time'], errors='coerce')
        sim_df = sim_df.sort_values(by='DateTime')
        
        balance = start_cap
        balances = [balance]
        dates = [sim_df['DateTime'].min() - pd.Timedelta(days=1) if not sim_df.empty else "Start"]
        
        for _, row in sim_df.iterrows():
            entry_price = row['Entry_Price']
            action_date = row['DateTime']
            pnl_points = row['PnL']
            
            cost_per_lot = entry_price * lot_size
            
            if "Fixed 1 Lot" in sim_mode:
                lots = 1
                if "bankrupt" in sim_mode and balance < cost_per_lot:
                    lots = 0
            else:
                lots = int(balance // cost_per_lot)
            
            # If we don't buy anything, balance doesn't change
            trade_pnl = pnl_points * lot_size * lots
            balance += trade_pnl
            
            balances.append(balance)
            dates.append(action_date)
            
        fig_eq = go.Figure()
        
        # Add horizontal baseline for starting capital
        fig_eq.add_hline(y=start_cap, line_width=1, line_dash="dash", line_color="gray")
        
        # Area chart style to match TradingView
        fig_eq.add_trace(go.Scatter(
            x=dates, 
            y=balances, 
            mode='lines', 
            line=dict(color='#26a69a' if balance >= start_cap else '#ef5350', width=2),
            fill='tozeroy' if min(balances) >= 0 else 'tonexty',
            fillcolor='rgba(38,166,154,0.1)' if balance >= start_cap else 'rgba(239,83,80,0.1)'
        ))
        
        end_color = "red" if balance < start_cap else "green"
        fig_eq.update_layout(title=dict(text=f"Final Balance: ₹{balance:,.2f}  |  Starting Capital: ₹{start_cap:,.2f}  |  Total ROI: {((balance - start_cap)/start_cap)*100:.1f}%", font=dict(color=end_color)), xaxis_title="Trade Date", yaxis_title="Account Balance (₹)", height=500, xaxis_rangeslider_visible=True, margin=dict(t=50, b=20, l=20, r=20))
        st.plotly_chart(fig_eq, use_container_width=True)

    with tab1:
        # Select Trade
        trade_options = filtered_df.apply(
            lambda row: f"ID: {row['Trade_ID']} | {row['Date']} ({row['Type']}) {row['Outcome']}", axis=1
        ).tolist()
        
        selected_trade_str = st.selectbox("Select Trade to Visualize", options=trade_options)
        
        # Parse selected Trade ID
        selected_id = int(selected_trade_str.split(" |")[0].replace("ID: ", ""))
        trade = filtered_df[filtered_df['Trade_ID'] == selected_id].iloc[0]

        # Display Trade Stats
        st.subheader(f"Trade Analysis for {trade['Date']} - {trade['Symbol']} ({trade['Type']})")
        
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Outcome", trade['Outcome'])
        col2.metric("PnL (Points)", round(trade['PnL'], 2))
        col3.metric("Case Trigger", trade['Case'])
        col4.metric("Risk (Points)", round(trade['Risk'], 2))
        col5.metric("Reward (Points)", round(trade['TP'] - trade['Entry_Price'], 2))
        
        st.markdown(f"**Signal Candle:** {trade['Signal_Time']} &nbsp;&nbsp;|&nbsp;&nbsp; **Entry Candle:** {trade['Entry_Time']} &nbsp;&nbsp;|&nbsp;&nbsp; **Exit Hit:** {trade['Exit_Time']}")
        st.markdown(f"**Entry Price:** ₹{trade['Entry_Price']} &nbsp;&nbsp;|&nbsp;&nbsp; **Stop Loss:** ₹{trade['SL']} &nbsp;&nbsp;|&nbsp;&nbsp; **Take Profit:** ₹{trade['TP']}")

        st.divider()

        # Load Data
        spot_df, opt_df = load_day_data(trade['Date'], trade['Symbol'])
        
        if spot_df is None or opt_df is None:
            st.error(f"Could not load historical 5-min CSV data for {trade['Date']} and {trade['Symbol']}.")
            return

        # Plot
        col_left, col_right = st.columns(2)
        
        with col_left:
            fig_spot = plot_candlestick(
                spot_df, title=f"Nifty 50 Spot ({trade['Date']})", 
                signal_time=trade['Signal_Time'],
                is_option=False
            )
            st.plotly_chart(fig_spot, use_container_width=True)
            
        with col_right:
            fig_opt = plot_candlestick(
                opt_df, title=f"{trade['Symbol']} ({trade['Type']})",
                signal_time=trade['Signal_Time'],
                entry_time=trade['Entry_Time'],
                exit_time=trade['Exit_Time'],
                entry_price=trade['Entry_Price'],
                sl=trade['SL'],
                tp=trade['TP'],
                is_option=True
            )
            st.plotly_chart(fig_opt, use_container_width=True)

if __name__ == "__main__":
    main()
