# discord_heatmap.py
import time
import pythoncom
from datetime import datetime, timedelta
import pytz
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests
from src.rtd.rtd_worker import RTDWorker
from src.utils.option_symbol_builder import OptionSymbolBuilder
from heatmap import plot_gex_heatmap

TICKER         = 'QQQ'
STRIKE_RANGE   = 20
STRIKE_SPACING = 1.0
WEBHOOK_URL    = 'https://discordapp.com/api/webhooks/your_webhook_here'
INTERVAL_MIN   = 5

NY_TZ = pytz.timezone('America/New_York')


def now_ny():
    return datetime.now(NY_TZ)


def get_expiries(n=5):
    today = now_ny().date()
    expiries = []
    d = today
    while len(expiries) < n:
        if d.weekday() < 5:
            expiries.append(d)
        d += timedelta(days=1)
    return expiries


def build_matrix(data, option_symbols, spot, strike_range=20):
    records = []
    for sym in option_symbols:
        try:
            inner    = sym.lstrip('.')[len(TICKER):]
            is_call  = 'C' in inner
            date_str, strike_str = inner.split('C') if is_call else inner.split('P')
            K        = float(strike_str)
            expiry   = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"
            sign     = 1 if is_call else -1
            if not (spot - strike_range <= K <= spot + strike_range):
                continue
            T = (datetime.strptime(expiry, '%Y-%m-%d') - datetime.now()).days / 365
            if T < -0.01:
                continue
            gamma = float(data.get(f"{sym}:GAMMA",    0) or 0)
            oi    = float(data.get(f"{sym}:OPEN_INT", 0) or 0)
            records.append({'strike': K, 'expiry': expiry,
                            'gex': sign * gamma * oi * 100 * spot})
        except Exception:
            continue

    if not records:
        return None

    df     = pd.DataFrame(records)
    matrix = df.groupby(['strike', 'expiry'])['gex'].sum().unstack('expiry').fillna(0)
    return matrix.sort_index(ascending=False)


def send_discord(image_path, spot):
    timestamp = now_ny().strftime("%Y-%m-%d %H:%M %Z")
    with open(image_path, 'rb') as f:
        requests.post(
            WEBHOOK_URL,
            data={'content': f'**{TICKER} GEX Heatmap** — {timestamp} — Spot: **${spot:.2f}**'},
            files={'file': (image_path, f, 'image/png')}
        )
    print(f"Sent to Discord ({timestamp})")


def run_once(expiry):
    from src.rtd.client import RTDClient
    from src.core.settings import SETTINGS
    from config.quote_types import QuoteType

    pythoncom.CoInitialize()
    client = None
    try:
        client = RTDClient(heartbeat_ms=SETTINGS['timing']['initial_heartbeat'])
        client.initialize()

        client.subscribe(QuoteType.LAST, TICKER)
        time.sleep(2)
        pythoncom.PumpWaitingMessages()

        with client._value_lock:
            values = dict(client._latest_values)

        spot_raw = values.get((TICKER, str(QuoteType.LAST))) or values.get((TICKER, 'LAST'))
        if not spot_raw:
            print("No spot price available")
            return

        spot = float(spot_raw.value if hasattr(spot_raw, 'value') else spot_raw)

        expiries    = get_expiries(5)
        option_syms = []
        for exp in expiries:
            syms = OptionSymbolBuilder.build_symbols(
                TICKER, exp, spot, STRIKE_RANGE, STRIKE_SPACING)
            option_syms.extend(syms)

        for sym in option_syms:
            client.subscribe(QuoteType.GAMMA,    sym)
            client.subscribe(QuoteType.OPEN_INT, sym)

        time.sleep(5)
        pythoncom.PumpWaitingMessages()

        with client._value_lock:
            values = dict(client._latest_values)

        data = {}
        for (sym, qt), quote in values.items():
            data[f"{sym}:{qt}"] = quote.value if hasattr(quote, 'value') else quote

        matrix = build_matrix(data, option_syms, spot, STRIKE_RANGE)
        if matrix is None or np.abs(matrix.values).max() == 0:
            print("Empty GEX matrix — skipping")
            return

        vmax = np.abs(matrix.values).max()
        fig, ax = plt.subplots(figsize=(10, 13))
        plot_gex_heatmap(matrix, spot, TICKER, fig=fig, ax=ax, vmax=vmax)
        plt.savefig('gex_heatmap.png', dpi=150, bbox_inches='tight', facecolor='#0a0a0a')
        plt.close(fig)
        send_discord('gex_heatmap.png', spot)

    finally:
        if client:
            try:
                client.Disconnect()
            except Exception:
                pass
        pythoncom.CoUninitialize()
        time.sleep(1)


if __name__ == '__main__':
    import traceback
    print(f"GEX bot started — interval: {INTERVAL_MIN} min")

    while True:
        now  = now_ny()
        mins = now.hour * 60 + now.minute
        is_open = (now.weekday() < 5 and (9 * 60 + 30) <= mins <= 16 * 60)

        if is_open:
            expiry = get_expiries(1)[0]
            try:
                run_once(expiry)
            except Exception as e:
                print(f"Error: {e}")
                traceback.print_exc()
            time.sleep(INTERVAL_MIN * 60)
        else:
            next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            if now >= next_open or now.weekday() >= 5:
                if now.weekday() == 5:
                    days_ahead = 2
                elif now.weekday() == 6:
                    days_ahead = 1
                else:
                    days_ahead = 1
                next_open = (now + timedelta(days=days_ahead)).replace(
                    hour=9, minute=30, second=0, microsecond=0)

            secs = (next_open - now).total_seconds()
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            print(f"Market closed — opens in {h}h {m}m")
            time.sleep(max(secs - 30, 60))
