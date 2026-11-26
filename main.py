# GBPJPY SMC SLP BOT - V2 REWRITE
# 1D Bias -> 4H Liquidity -> 30M SLP Entry
# Data: TwelveData API (No Yahoo, No Block)
# Output: Telegram Signal

import os
import requests
import pandas as pd
import time
from datetime import datetime

# --- CONFIG ---
API_KEY = os.getenv("TWELVE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = "GBP/JPY"

# Risk
SL_BUFFER_PIPS = 10
MAX_SWEEP_PIPS = 50

# --- DATA ---
def get_data(interval, outputsize=200):
    """interval: 1day, 4h, 30min"""
    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={interval}&outputsize={outputsize}&apikey={API_KEY}&format=JSON"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=20)
            data = r.json()
            if "values" not in data:
                print(f"TwelveData Error {interval}: {data}")
                time.sleep(5)
                continue
            df = pd.DataFrame(data["values"])
            # Convert to float and sort old -> new
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)
            return df
        except Exception as e:
            print(f"Fetch {interval} try {attempt+1} failed: {e}")
            time.sleep(5)
    return None

# --- HELPERS ---
def get_swing_highs_lows(df, lookback=5):
    """Find swing highs/lows"""
    highs = []
    lows = []
    for i in range(lookback, len(df)-lookback):
        if df["high"].iloc[i] == df["high"].iloc[i-lookback:i+lookback+1].max():
            highs.append((i, df["high"].iloc[i]))
        if df["low"].iloc[i] == df["low"].iloc[i-lookback:i+lookback+1].min():
            lows.append((i, df["low"].iloc[i]))
    return highs, lows

def detect_sweep_and_mss(df_30m, level, direction):
    """
    direction: 'bullish' = sweep low then go up, 'bearish' = sweep high then go down
    Returns: (sweep_found, mss_found, sweep_candle_idx, sweep_price, ob_price)
    """
    # Check last 10 candles for sweep
    for i in range(len(df_30m)-10, len(df_30m)-1):
        candle = df_30m.iloc[i]
        prev_candles = df_30m.iloc[max(0,i-5):i]
        
        if direction == "bullish":
            # Sweep = low goes BELOW level, but close back ABOVE level
            if candle["low"] < level and candle["close"] > level:
                sweep_price = candle["low"]
                sweep_idx = i
                # Check MSS after sweep: close above last Lower High
                # Last Lower High = max high of 5 candles before sweep
                last_lh = prev_candles["high"].max()
                for j in range(i+1, len(df_30m)):
                    if df_30m["close"].iloc[j] > last_lh:
                        # MSS confirmed
                        # OB = last bearish candle before MSS
                        ob_candle = df_30m.iloc[j-1]
                        return True, True, sweep_idx, sweep_price, ob_candle["low"]
                return True, False, sweep_idx, sweep_price, None
        else:
            # Bearish sweep
            if candle["high"] > level and candle["close"] < level:
                sweep_price = candle["high"]
                sweep_idx = i
                last_hl = prev_candles["low"].min()
                for j in range(i+1, len(df_30m)):
                    if df_30m["close"].iloc[j] < last_hl:
                        ob_candle = df_30m.iloc[j-1]
                        return True, True, sweep_idx, sweep_price, ob_candle["high"]
                return True, False, sweep_idx, sweep_price, None
    return False, False, None, None, None

def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        print("No Telegram token, printing only:")
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram failed: {e}")

# --- MAIN LOGIC ---
def main():
    print(f"--- Bot Run: {datetime.now()} ---")
    
    df_1d = get_data("1day", 50)
    df_4h = get_data("4h", 100)
    df_30m = get_data("30min", 200)
    
    if df_1d is None or df_4h is None or df_30m is None:
        print("Failed to get data, aborting")
        return

    # 1. 1D BIAS
    # Simple: If last 3 daily closes going up = Bullish, down = Bearish
    last_closes = df_1d["close"].tail(3).values
    if last_closes[-1] > last_closes[0]:
        bias = "Bullish"
    elif last_closes[-1] < last_closes[0]:
        bias = "Bearish"
    else:
        bias = "Range"
    
    # 2. 4H LIQUIDITY LEVELS
    highs_4h, lows_4h = get_swing_highs_lows(df_4h)
    last_4h_high = highs_4h[-1][1] if highs_4h else df_4h["high"].max()
    last_4h_low = lows_4h[-1][1] if lows_4h else df_4h["low"].min()
    current_price = df_30m["close"].iloc[-1]
    
    print(f"Bias: {bias} | Price: {current_price} | 4H High: {last_4h_high} | 4H Low: {last_4h_low}")

    signal = None
    setup_type = ""
    entry = sl = tp1 = tp2 = None

    # 3. CHECK SLP SETUPS
    # Continuation BUY: Bias Bullish, sweep 4H Low, MSS up
    if bias == "Bullish":
        sweep, mss, idx, sweep_price, ob = detect_sweep_and_mss(df_30m, last_4h_low, "bullish")
        if sweep and mss:
            setup_type = "CONTINUATION BUY SLP"
            entry = ob if ob else current_price
            sweep_pips = (entry - sweep_price) * 100  # JPY pips
            if sweep_pips > MAX_SWEEP_PIPS:
                print(f"Sweep too big {sweep_pips} pips, skip")
                return
            sl = sweep_price - (SL_BUFFER_PIPS / 100)
            tp1 = last_4h_high
            risk = entry - sl
            tp2 = entry + (risk * 3)
            signal = "BUY"
    
    # Continuation SELL: Bias Bearish, sweep 4H High, MSS down
    if bias == "Bearish" and not signal:
        sweep, mss, idx, sweep_price, ob = detect_sweep_and_mss(df_30m, last_4h_high, "bearish")
        if sweep and mss:
            setup_type = "CONTINUATION SELL SLP"
            entry = ob if ob else current_price
            sweep_pips = (sweep_price - entry) * 100
            if sweep_pips > MAX_SWEEP_PIPS:
                print(f"Sweep too big, skip")
                return
            sl = sweep_price + (SL_BUFFER_PIPS / 100)
            tp1 = last_4h_low
            risk = sl - entry
            tp2 = entry - (risk * 3)
            signal = "SELL"

    # Reversal: Only if price at Daily extreme
    # Bullish Reversal: Sweep Daily Low, MSS up, even if 1D was Bearish
    if not signal:
        daily_low = df_1d["low"].min()
        daily_high = df_1d["high"].max()
        # Check if price swept daily low recently
        sweep, mss, idx, sweep_price, ob = detect_sweep_and_mss(df_30m, daily_low, "bullish")
        if sweep and mss and current_price < df_1d["close"].iloc[-2]:
            setup_type = "REVERSAL BUY SLP (Daily Low Sweep)"
            entry = ob if ob else current_price
            sl = sweep_price - (SL_BUFFER_PIPS / 100)
            tp1 = last_4h_high
            risk = entry - sl
            tp2 = entry + (risk * 3)
            signal = "BUY"

    if signal:
        msg = (
            f"<b>🚨 GBPJPY {signal} - {setup_type}</b>\n\n"
            f"<b>Bias 1D:</b> {bias}\n"
            f"<b>Price:</b> {current_price:.2f}\n"
            f"<b>Entry:</b> {entry:.2f} (Retest OB)\n"
            f"<b>SL:</b> {sl:.2f}\n"
            f"<b>TP1:</b> {tp1:.2f} (4H Liquidity - Take 50%)\n"
            f"<b>TP2:</b> {tp2:.2f} (1:3 Runner)\n\n"
            f"Invalidation: If price closes back inside OB opposite way, trade dead."
        )
        send_telegram(msg)
        print("Signal sent!")
    else:
        print("NO TRADE - No SLP + MSS found")
        # Optional: Send no-trade to debug, comment out if you no want spam
        # send_telegram(f"GBPJPY No Trade - Bias {bias} Price {current_price:.2f}")

if __name__ == "__main__":
    main()
