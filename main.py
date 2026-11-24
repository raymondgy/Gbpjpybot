
# SLP BOT V6 - GBPJPY + EURUSD - LIMIT ORDER - FIXED FOR RENDER
import os, time, threading, requests
import yfinance as yf
import pandas as pd
from flask import Flask
from datetime import datetime
import pytz

app = Flask(__name__)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(e)

def get_session():
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except Exception as e:
        print("curl_cffi not found, using default:", e)
        return None

def download(ticker, interval, period):
    for attempt in range(3):
        try:
            session = get_session()
            df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True, session=session, threads=False)
            if df is not None and not df.empty:
                return df
            print(f"Empty data attempt {attempt+1} for {ticker} {interval}")
        except Exception as e:
            print(f"Download fail {attempt+1} {ticker} {interval}:", e)
        time.sleep(5)
    return None

def fix(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).capitalize() for c in df.columns]
    return df

# PAIR SETTINGS
PAIRS = {
    "GBPJPY": {"ticker": "GBPJPY=X", "decimals": 2, "offset": 0.03, "sl_buffer": 0.10, "min_risk": 0.10, "max_risk": 0.50, "tolerance": 0.05, "sweep_range": 1.0},
    "EURUSD": {"ticker": "EURUSD=X", "decimals": 5, "offset": 0.0003, "sl_buffer": 0.0010, "min_risk": 0.0010, "max_risk": 0.0050, "tolerance": 0.0005, "sweep_range": 0.01},
}

def check_pair(pair, cfg):
    ticker = cfg["ticker"]
    dec = cfg["decimals"]
    print(f"Checking {pair}...")

    df30 = download(ticker, "30m", "60d")
    if df30 is None: print(f"No 30m data {pair}"); return False
    df30 = fix(df30)
    try: df30.index = df30.index.tz_convert('Africa/Lagos')
    except: pass

    df1h = download(ticker, "1h", "6mo")
    if df1h is None: print(f"No 1h data {pair}"); return False
    df1h = fix(df1h)
    df4h = df1h.resample('4h').agg({'Open':'first','High':'max','Low':'min','Close':'last'}).dropna()

    df1d = download(ticker, "1d", "6mo")
    if df1d is None: print(f"No 1d data {pair}"); return False
    df1d = fix(df1d)
    df1d['EMA20'] = df1d['Close'].ewm(span=20).mean()
    bias = "Bullish" if float(df1d['Close'].iloc[-1]) > float(df1d['EMA20'].iloc[-1]) else "Bearish"

    h4_highs = df4h['High'].tail(30).tolist()
    h4_lows = df4h['Low'].tail(30).tolist()
    sr = cfg["sweep_range"]

    for i in range(len(df30)-8, len(df30)-3):
        ch = float(df30['High'].iloc[i]); cl = float(df30['Low'].iloc[i]); cc = float(df30['Close'].iloc[i])
        swept_low = next((float(x) for x in h4_lows if cl < float(x) < cl+sr), None)
        swept_high = next((float(x) for x in h4_highs if ch-sr < float(x) < ch), None)

        # BEARISH - SELL LIMIT
        if bias=="Bearish" and swept_high and ch > swept_high and cc < swept_high:
            if float(df30['Close'].iloc[i+2]) < float(df30['Low'].iloc[i+1]):
                ob_l = float(df30['Low'].iloc[i+1]); ob_h = float(df30['High'].iloc[i+1])
                if ob_l <= float(df30['High'].iloc[-1]) <= ob_h + cfg["tolerance"]:
                    entry = (ob_h+ob_l)/2 - cfg["offset"]; sl = ob_h + cfg["sl_buffer"]; risk = sl-entry
                    if cfg["min_risk"] < risk < cfg["max_risk"]:
                        tp1 = entry - risk*2
                        pips = risk*100 if pair=="GBPJPY" else risk*10000
                        send(f"🔴 *SELL LIMIT {pair} SLP*\nType: SELL LIMIT - Wait for price to come up\nEntry: {entry:.{dec}f}\nSL: {sl:.{dec}f} ({pips:.0f} pips)\nTP1: {tp1:.{dec}f} (2R)\nBias: {bias}\n\n👉 Set SELL LIMIT for demo, no market order.")
                        return True

        # BULLISH - BUY LIMIT
        if bias=="Bullish" and swept_low and cl < swept_low and cc > swept_low:
            if float(df30['Close'].iloc[i+2]) > float(df30['High'].iloc[i+1]):
                ob_l = float(df30['Low'].iloc[i+1]); ob_h = float(df30['High'].iloc[i+1])
                if ob_l - cfg["tolerance"] <= float(df30['Low'].iloc[-1]) <= ob_h:
                    entry = (ob_h+ob_l)/2 + cfg["offset"]; sl = ob_l - cfg["sl_buffer"]; risk = entry-sl
                    if cfg["min_risk"] < risk < cfg["max_risk"]:
                        tp1 = entry + risk*2
                        pips = risk*100 if pair=="GBPJPY" else risk*10000
                        send(f"🟢 *BUY LIMIT {pair} SLP*\nType: BUY LIMIT - Wait for price to come down\nEntry: {entry:.{dec}f}\nSL: {sl:.{dec}f} ({pips:.0f} pips)\nTP1: {tp1:.{dec}f} (2R)\nBias: {bias}\n\n👉 Set BUY LIMIT for demo, no market order.")
                        return True
    print(f"No signal {pair}. Bias: {bias}")
    return False

def check():
    try:
        print("Checking...", datetime.now())
        now_hour = datetime.now(pytz.timezone('Africa/Lagos')).hour
        if now_hour < 8 or now_hour > 18:
            print("Outside session"); return

        for pair, cfg in PAIRS.items():
            if check_pair(pair, cfg):
                print(f"Signal sent for {pair}, waiting next cycle")
                break
            time.sleep(5)
    except Exception as e:
        print("Error:", e)

def telegram_poll():
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset: params["offset"] = offset
            r = requests.get(url, params=params, timeout=35).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                text = msg.get("text", "")
                if text == "/start":
                    send("✅ Bot is online! I go send GBPJPY + EURUSD SLP LIMIT signals 8am-6pm Lagos when setup appear.")
        except Exception as e:
            print("Poll error:", e)
            time.sleep(5)

def loop():
    send("✅ *SLP Bot V6 is LIVE on Render*\nPairs: GBPJPY + EURUSD\nType: LIMIT ORDERS ONLY\nChecking every 30 mins, 8am-6pm Lagos.")
    while True:
        check()
        time.sleep(1800)

@app.route('/')
def home(): return "Bot Running!"

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=telegram_poll, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
