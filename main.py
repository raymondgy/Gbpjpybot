# GBPJPY SLP LIVE BOT - V4 LONDON FILTER
import os
import requests
import pandas as pd
from datetime import datetime, timezone

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
SYMBOL = "GBP/JPY"

# V4 Filters
MAX_SWEEP_PIPS = 30  # V4: was 50, now 30 - small sweep only
MIN_TP1_PIPS = 20   # TP1 must be at least 20 pips away

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not set, printing only")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram sent: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_data(interval, outputsize=500):
    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_API_KEY}&format=JSON"
    try:
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
    except Exception as e:
        print(f"Fetch error {interval}: {e}")
        return None

def get_swing_levels(df_4h):
    # Last swing high/low from last 50 candles
    highs = []
    lows = []
    lookback = 3
    df = df_4h.tail(50).reset_index(drop=True)
    for i in range(lookback, len(df)-lookback):
        if df["high"].iloc[i] == df["high"].iloc[i-lookback:i+lookback+1].max():
            highs.append(df["high"].iloc[i])
        if df["low"].iloc[i] == df["low"].iloc[i-lookback:i+lookback+1].min():
            lows.append(df["low"].iloc[i])
    if not highs or not lows:
        return None, None
    return highs[-1], lows[-1]

def main():
    now = datetime.now(timezone.utc)
    print(f"--- Bot Run V4: {now} UTC ---")

    # --- V4 FILTER 1: LONDON SESSION ONLY ---
    # GitHub server time is UTC. London 8am-5pm = 7am-4pm UTC in winter
    current_hour = now.hour
    if current_hour < 7 or current_hour > 16:
        print(f"Outside London session (Hour UTC: {current_hour}). No trade. Sleeping.")
        return

    # --- FETCH DATA ---
    df_1d = get_data("1day", 50)
    df_4h = get_data("4h", 200)
    df_30m = get_data("30min", 500)

    if df_1d is None or df_4h is None or df_30m is None:
        print("Failed to fetch data")
        return

    # --- 1. DAILY BIAS ---
    last_3_closes = df_1d["close"].tail(3).values
    bias = "Bullish" if last_3_closes[-1] > last_3_closes[0] else "Bearish"
    print(f"1D Bias: {bias} | Last closes: {last_3_closes}")

    # --- 2. 4H LEVELS ---
    h4_high, h4_low = get_swing_levels(df_4h)
    if h4_high is None:
        print("No 4H swing found")
        return
    print(f"4H High: {h4_high:.2f} | 4H Low: {h4_low:.2f}")

    # --- 3. 30M SWEEP + MSS ---
    # Look at last 20 candles for setup
    window = df_30m.tail(20).reset_index(drop=True)
    current_price = df_30m["close"].iloc[-1]
    print(f"Current Price: {current_price:.2f}")

    signal = None
    entry = sl = tp1 = tp2 = None
    sweep_pips = 0

    # BUY SETUP - Bullish bias only
    if bias == "Bullish":
        for j in range(5, len(window)-2):
            candle = window.iloc[j]
            prev_low = window.iloc[j-3:j]["low"].min()
            # Sweep: low below prev low, close back above
            if candle["low"] < prev_low and candle["close"] > prev_low:
                last_lh = window.iloc[max(0, j-5):j]["high"].max()
                # Check MSS after sweep
                for k in range(j+1, len(window)):
                    if window["close"].iloc[k] > last_lh:
                        entry = window["close"].iloc[k]
                        sweep_low = candle["low"]
                        sweep_pips = (entry - sweep_low) * 100

                        # V4 FILTER 2: Small sweep only
                        if sweep_pips > MAX_SWEEP_PIPS:
                            print(f"BUY sweep too big: {sweep_pips:.1f} pips > {MAX_SWEEP_PIPS}, skip")
                            break

                        sl = sweep_low - 0.10
                        tp1 = h4_high

                        # V4 FILTER 3: TP1 must be at least 20 pips away
                        if (tp1 - entry) * 100 < MIN_TP1_PIPS:
                            print(f"BUY TP1 too close: {(tp1-entry)*100:.1f} pips, skip")
                            break

                        risk = entry - sl
                        tp2 = entry + (risk * 3)
                        signal = "BUY"
                        print(f"BUY FOUND: Entry {entry:.2f} Sweep {sweep_pips:.1f} pips")
                        break
                if signal:
                    break

    # SELL SETUP - Bearish bias only
    if bias == "Bearish" and not signal:
        for j in range(5, len(window)-2):
            candle = window.iloc[j]
            prev_high = window.iloc[j-3:j]["high"].max()
            # Sweep: high above prev high, close back below
            if candle["high"] > prev_high and candle["close"] < prev_high:
                last_hl = window.iloc[max(0, j-5):j]["low"].min()
                for k in range(j+1, len(window)):
                    if window["close"].iloc[k] < last_hl:
                        entry = window["close"].iloc[k]
                        sweep_high = candle["high"]
                        sweep_pips = (sweep_high - entry) * 100

                        if sweep_pips > MAX_SWEEP_PIPS:
                            print(f"SELL sweep too big: {sweep_pips:.1f} pips > {MAX_SWEEP_PIPS}, skip")
                            break

                        sl = sweep_high + 0.10
                        tp1 = h4_low

                        if (entry - tp1) * 100 < MIN_TP1_PIPS:
                            print(f"SELL TP1 too close: {(entry-tp1)*100:.1f} pips, skip")
                            break

                        risk = sl - entry
                        tp2 = entry - (risk * 3)
                        signal = "SELL"
                        print(f"SELL FOUND: Entry {entry:.2f} Sweep {sweep_pips:.1f} pips")
                        break
                if signal:
                    break

    # --- 4. SEND SIGNAL ---
    if not signal:
        print("No V4 setup found. Waiting...")
        return

    # Calculate pips for message
    risk_pips = abs(entry - sl) * 100
    tp1_pips = abs(tp1 - entry) * 100

    msg = (
        f"🚨 *GBPJPY SLP V4 SIGNAL* 🚨\n\n"
        f"*{signal} {SYMBOL}*\n"
        f"Bias: {bias} (1D)\n"
        f"Setup: Sweep + MSS - London Session\n"
        f"Sweep Size: {sweep_pips:.1f} pips\n\n"
        f"Entry: `{entry:.2f}`\n"
        f"SL: `{sl:.2f}` ({risk_pips:.0f} pips)\n"
        f"TP1: `{tp1:.2f}` ({tp1_pips:.0f} pips)\n"
        f"TP2: `{tp2:.2f}` (3R)\n\n"
        f"Risk: 1% | Move SL to BE at TP1\n"
        f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg)
    print("Signal sent!")

if __name__ == "__main__":
    main()
