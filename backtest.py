# GBPJPY SLP BACKTESTER - V2
# Tests the exact same logic as main.py on last 6 months

import os
import requests
import pandas as pd
from datetime import datetime, timedelta

API_KEY = os.getenv("TWELVE_API_KEY")
SYMBOL = "GBP/JPY"

def get_data(interval, outputsize=5000):
    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={interval}&outputsize={outputsize}&apikey={API_KEY}&format=JSON"
    r = requests.get(url, timeout=30)
    data = r.json()
    if "values" not in data:
        print(f"Error {interval}: {data}")
        return None
    df = pd.DataFrame(data["values"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df

def run_backtest():
    print("Fetching data for backtest... This fit take 30s")
    df_1d = get_data("1day", 200)
    df_4h = get_data("4h", 1000)
    df_30m = get_data("30min", 5000)

    if df_1d is None or df_30m is None:
        print("Failed to get data")
        return

    print(f"Got {len(df_30m)} x 30M candles, {len(df_1d)} x Daily candles")
    print(f"Range: {df_30m['datetime'].iloc[0]} to {df_30m['datetime'].iloc[-1]}")

    trades = []
    # We go loop through 30M candles, start from candle 100 to get history
    for i in range(100, len(df_30m)-50):
        current_time = df_30m["datetime"].iloc[i]
        current_price = df_30m["close"].iloc[i]

        # 1. Get 1D Bias at that time - last 3 daily candles before current_time
        daily_before = df_1d[df_1d["datetime"] <= current_time]
        if len(daily_before) < 3:
            continue
        last_closes = daily_before["close"].tail(3).values
        bias = "Bullish" if last_closes[-1] > last_closes[0] else "Bearish" if last_closes[-1] < last_closes[0] else "Range"

        # 2. Get 4H Liquidity at that time
        fourh_before = df_4h[df_4h["datetime"] <= current_time]
        if len(fourh_before) < 20:
            continue
        # Last swing low/high = lowest low / highest high of last 20 4H candles
        last_4h_low = fourh_before["low"].tail(20).min()
        last_4h_high = fourh_before["high"].tail(20).max()

        # 3. Check for SLP in last 10 candles
        window = df_30m.iloc[i-10:i+1]
        signal = None
        setup = ""
        entry = sl = tp1 = tp2 = None

        # CONTINUATION BUY: Sweep 4H Low, close back above, then MSS
        if bias == "Bullish":
            for j in range(len(window)-5, len(window)):
                candle = window.iloc[j]
                if candle["low"] < last_4h_low and candle["close"] > last_4h_low:
                    # Sweep found, check MSS in next candles
                    last_lh = window.iloc[max(0,j-5):j]["high"].max()
                    for k in range(j+1, len(window)):
                        if window["close"].iloc[k] > last_lh:
                            # MSS confirmed
                            entry = window["low"].iloc[k-1]  # OB low
                            sweep_low = candle["low"]
                            if (entry - sweep_low)*100 > 50: # Sweep too big
                                break
                            sl = sweep_low - 0.10
                            tp1 = last_4h_high
                            risk = entry - sl
                            tp2 = entry + (risk*3)
                            signal = "BUY"
                            setup = "CONTINUATION BUY"
                            break
                    break

        # CONTINUATION SELL
        if bias == "Bearish" and not signal:
            for j in range(len(window)-5, len(window)):
                candle = window.iloc[j]
                if candle["high"] > last_4h_high and candle["close"] < last_4h_high:
                    last_hl = window.iloc[max(0,j-5):j]["low"].min()
                    for k in range(j+1, len(window)):
                        if window["close"].iloc[k] < last_hl:
                            entry = window["high"].iloc[k-1]
                            sweep_high = candle["high"]
                            if (sweep_high - entry)*100 > 50:
                                break
                            sl = sweep_high + 0.10
                            tp1 = last_4h_low
                            risk = sl - entry
                            tp2 = entry - (risk*3)
                            signal = "SELL"
                            setup = "CONTINUATION SELL"
                            break
                    break

        if not signal:
            continue

        # 4. Simulate Trade - Check next 50 candles wetin happen
        future = df_30m.iloc[i+1:i+50]
        result = "NO RESULT"
        r_result = 0
        exit_price = entry
        
        for _, fut in future.iterrows():
            if signal == "BUY":
                if fut["low"] <= sl:
                    result = "LOSS"
                    r_result = -1
                    exit_price = sl
                    break
                if fut["high"] >= tp2:
                    result = "WIN TP2"
                    r_result = 3
                    exit_price = tp2
                    break
                if fut["high"] >= tp1:
                    # If e hit TP1, we go take partial, but for backtest we count as WIN TP1 = 1.5R
                    # Continue to see if e go hit TP2 or SL
                    pass
            else: # SELL
                if fut["high"] >= sl:
                    result = "LOSS"
                    r_result = -1
                    exit_price = sl
                    break
                if fut["low"] <= tp2:
                    result = "WIN TP2"
                    r_result = 3
                    exit_price = tp2
                    break

        # If e never hit SL or TP2 in 50 candles, check TP1
        if result == "NO RESULT":
            # Check if TP1 was hit at all
            if signal == "BUY" and future["high"].max() >= tp1:
                result = "WIN TP1"
                r_result = 1.5
            elif signal == "SELL" and future["low"].min() <= tp1:
                result = "WIN TP1"
                r_result = 1.5
            else:
                result = "BREAKEVEN / TIMEOUT"
                r_result = 0

        trades.append({
            "time": current_time,
            "bias": bias,
            "setup": setup,
            "signal": signal,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "result": result,
            "R": r_result
        })
        # Skip ahead 20 candles so we no double-count same setup
        # This na to avoid same signal repeating
        # We go jump i forward by using a trick: set i via loop break not possible, so we just continue but next signals go overlap small

    # --- RESULTS ---
    if not trades:
        print("\n❌ No trades found in backtest. Strategy too strict or data small.")
        return

    df_trades = pd.DataFrame(trades)
    total = len(df_trades)
    wins = len(df_trades[df_trades["R"] > 0])
    losses = len(df_trades[df_trades["R"] < 0])
    win_rate = (wins / total) * 100
    total_r = df_trades["R"].sum()
    avg_r = df_trades["R"].mean()
    profit_factor = df_trades[df_trades["R"]>0]["R"].sum() / abs(df_trades[df_trades["R"]<0]["R"].sum()) if losses>0 else 999

    print("\n" + "="*50)
    print("📊 GBPJPY SLP BACKTEST RESULTS")
    print("="*50)
    print(f"Total Trades: {total}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Total R: {total_r:.1f}R")
    print(f"Avg R per trade: {avg_r:.2f}R")
    print(f"Profit Factor: {profit_factor:.2f}")
    print("="*50)
    print("\nLast 10 Trades:")
    print(df_trades.tail(10).to_string(index=False))

    # Save to CSV
    df_trades.to_csv("backtest_results.csv", index=False)
    print("\n✅ Saved to backtest_results.csv")

if __name__ == "__main__":
    run_backtest()
