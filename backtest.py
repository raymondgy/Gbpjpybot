# GBPJPY SLP BACKTESTER - V4 LONDON FILTER
import os
import requests
import pandas as pd

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

def get_swing(df, lookback=3):
    highs = []
    lows = []
    for i in range(lookback, len(df)-lookback):
        if df["high"].iloc[i] == df["high"].iloc[i-lookback:i+lookback+1].max():
            highs.append(df["high"].iloc[i])
        if df["low"].iloc[i] == df["low"].iloc[i-lookback:i+lookback+1].min():
            lows.append(df["low"].iloc[i])
    return highs, lows

def run_backtest():
    print("Fetching data...")
    df_1d = get_data("1day", 200)
    df_4h = get_data("4h", 1000)
    df_30m = get_data("30min", 5000)
    print(f"Got {len(df_30m)} candles")
    print(f"Range: {df_30m['datetime'].iloc[0]} to {df_30m['datetime'].iloc[-1]}")

    trades = []
    i = 100
    skipped_night = 0
    skipped_big_sweep = 0

    while i < len(df_30m)-60:
        current_time = df_30m["datetime"].iloc[i]
        
        # --- V4 FILTER 1: LONDON SESSION ONLY ---
        # TwelveData time is UTC. London 8am-5pm = 7am-4pm UTC (winter)
        # We go allow 7am to 16pm UTC
        hour = current_time.hour
        if hour < 7 or hour > 16:
            skipped_night += 1
            i += 1
            continue

        # 1D Bias
        daily_before = df_1d[df_1d["datetime"] <= current_time]
        if len(daily_before) < 3:
            i += 1
            continue
        last_closes = daily_before["close"].tail(3).values
        bias = "Bullish" if last_closes[-1] > last_closes[0] else "Bearish"

        # 4H Levels
        fourh_before = df_4h[df_4h["datetime"] <= current_time]
        if len(fourh_before) < 30:
            i += 1
            continue
        h4_highs, h4_lows = get_swing(fourh_before.tail(50))
        if not h4_highs or not h4_lows:
            i += 1
            continue
        last_4h_high = h4_highs[-1]
        last_4h_low = h4_lows[-1]

        window = df_30m.iloc[i-20:i+1]
        signal = None
        setup = ""
        entry = sl = tp1 = tp2 = None

        # BUY
        if bias == "Bullish":
            for j in range(5, len(window)-2):
                candle = window.iloc[j]
                prev_low = window.iloc[j-3:j]["low"].min()
                if candle["low"] < prev_low and candle["close"] > prev_low:
                    last_lh = window.iloc[max(0,j-5):j]["high"].max()
                    for k in range(j+1, len(window)):
                        if window["close"].iloc[k] > last_lh:
                            entry = window["close"].iloc[k]
                            sweep_low = candle["low"]
                            sweep_pips = (entry - sweep_low)*100
                            # --- V4 FILTER 2: SMALL SWEEP ONLY ---
                            if sweep_pips > 30:
                                skipped_big_sweep += 1
                                break
                            sl = sweep_low - 0.10
                            tp1 = last_4h_high
                            if (tp1 - entry)*100 < 20: # TP1 must be at least 20 pips away
                                break
                            risk = entry - sl
                            tp2 = entry + (risk*3)
                            signal = "BUY"
                            setup = "BUY SLP London"
                            break
                    if signal:
                        break

        # SELL
        if bias == "Bearish" and not signal:
            for j in range(5, len(window)-2):
                candle = window.iloc[j]
                prev_high = window.iloc[j-3:j]["high"].max()
                if candle["high"] > prev_high and candle["close"] < prev_high:
                    last_hl = window.iloc[max(0,j-5):j]["low"].min()
                    for k in range(j+1, len(window)):
                        if window["close"].iloc[k] < last_hl:
                            entry = window["close"].iloc[k]
                            sweep_high = candle["high"]
                            sweep_pips = (sweep_high - entry)*100
                            if sweep_pips > 30:
                                skipped_big_sweep += 1
                                break
                            sl = sweep_high + 0.10
                            tp1 = last_4h_low
                            if (entry - tp1)*100 < 20:
                                break
                            risk = sl - entry
                            tp2 = entry - (risk*3)
                            signal = "SELL"
                            setup = "SELL SLP London"
                            break
                    if signal:
                        break

        if not signal:
            i += 1
            continue

        # Simulate
        future = df_30m.iloc[i+1:i+60]
        result = "NO RESULT"
        r_result = 0
        for _, fut in future.iterrows():
            if signal == "BUY":
                if fut["low"] <= sl:
                    result = "LOSS"; r_result = -1; break
                if fut["high"] >= tp2:
                    result = "WIN TP2"; r_result = 3; break
            else:
                if fut["high"] >= sl:
                    result = "LOSS"; r_result = -1; break
                if fut["low"] <= tp2:
                    result = "WIN TP2"; r_result = 3; break
        if result == "NO RESULT":
            if signal == "BUY" and future["high"].max() >= tp1:
                result = "WIN TP1"; r_result = 1.5
            elif signal == "SELL" and future["low"].min() <= tp1:
                result = "WIN TP1"; r_result = 1.5
            else:
                result = "TIMEOUT"; r_result = 0

        trades.append({"time": current_time, "bias": bias, "setup": setup, "signal": signal, "entry": round(entry,2), "sl": round(sl,2), "tp1": round(tp1,2), "tp2": round(tp2,2), "result": result, "R": r_result})
        i += 20
        continue

    if not trades:
        print("\n❌ No trades")
        return

    df_trades = pd.DataFrame(trades)
    total = len(df_trades)
    wins = len(df_trades[df_trades["R"] > 0])
    losses = len(df_trades[df_trades["R"] < 0])
    win_rate = (wins/total)*100
    total_r = df_trades["R"].sum()
    avg_r = df_trades["R"].mean()
    # Profit Factor
    gross_win = df_trades[df_trades["R"]>0]["R"].sum()
    gross_loss = abs(df_trades[df_trades["R"]<0]["R"].sum())
    pf = gross_win / gross_loss if gross_loss>0 else 999

    print("\n" + "="*50)
    print("📊 GBPJPY SLP BACKTEST V4 - LONDON FILTER")
    print("="*50)
    print(f"Total Trades: {total} (V3 was 108)")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Win Rate: {win_rate:.1f}% (V3 was 39.8%)")
    print(f"Total R: {total_r:.1f}R (V3 was 34.0R)")
    print(f"Avg R: {avg_r:.2f}R")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Skipped Night Trades: {skipped_night}")
    print(f"Skipped Big Sweep: {skipped_big_sweep}")
    print("="*50)
    print(df_trades.tail(15).to_string(index=False))
    df_trades.to_csv("backtest_results.csv", index=False)

if __name__ == "__main__":
    run_backtest()
