import os, yfinance as yf, requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})

# 1. Connect Message
send("✅ *GBPJPY Bot Connected*\nLimit Order Mode ON\nWaiting for sweep...")

try:
    df = yf.download("GBPJPY=X", period="2d", interval="15m", progress=False)
    if df.empty:
        send("⚠️ No GBPJPY data")
        raise SystemExit

    # Flatten if MultiIndex
    if hasattr(df.columns, 'get_level_values'):
        try:
            df.columns = df.columns.get_level_values(0)
        except: pass

    price = float(df['Close'].iloc[-1])
    high = float(df['High'].max())
    low = float(df['Low'].max()) if False else float(df['Low'].min())

    # Use previous day High/Low for CRT
    # Last 96 candles = 1 day of 15m
    prev = df.iloc[:-96] if len(df) > 96 else df
    prev_high = float(prev['High'].max())
    prev_low = float(prev['Low'].min())

    msg = f"📊 *GBPJPY CRT - Limit Order Mode*\nPrice: ${price:.2f}\nPrev High: {prev_high:.2f}\nPrev Low: {prev_low:.2f}\n\n"

    # Sweep Low -> Buy Limit
    if price < prev_low:
        entry = round(prev_low + 0.15, 2)
        sl = round(entry - 0.35, 2)
        tp1 = round(entry + 0.35, 2)
        tp2 = round(entry + 0.70, 2)
        tp3 = round(entry + 1.05, 2)
        msg += f"🟢 *BUY LIMIT if sweep Low*\nEntry: {entry}\nSL: {sl}\nTP1: {tp1}\nTP2: {tp2}\nTP3: {tp3}\n⚠️ Wait for candle close, no market order"

    # Sweep High -> Sell Limit
    elif price > prev_high:
        entry = round(prev_high - 0.15, 2)
        sl = round(entry + 0.35, 2)
        tp1 = round(entry - 0.35, 2)
        tp2 = round(entry - 0.70, 2)
        tp3 = round(entry - 1.05, 2)
        msg += f"🔴 *SELL LIMIT if sweep High*\nEntry: {entry}\nSL: {sl}\nTP1: {tp1}\nTP2: {tp2}\nTP3: {tp3}\n⚠️ Wait for candle close, no market order"

    else:
        # No sweep yet - show pending limits like Gold bot
        buy_entry = round(prev_low + 0.15, 2)
        sell_entry = round(prev_high - 0.15, 2)
        msg += f"🟡 *NO SWEEP YET - WAIT*\n\n🟢 Buy Limit if sweep Low: {buy_entry} (SL {round(buy_entry-0.35,2)})\n🔴 Sell Limit if sweep High: {sell_entry} (SL {round(sell_entry+0.35,2)})"

    send(msg)

except Exception as e:
    send(f"❌ GBPJPY Error: {e}")
