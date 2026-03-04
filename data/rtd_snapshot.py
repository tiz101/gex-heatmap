# data/rtd_snapshot.py
import time
import pythoncom
from datetime import date, timedelta
from src.rtd.client import RTDClient
from src.utils.option_symbol_builder import OptionSymbolBuilder
from config.quote_types import QuoteType
from src.core.settings import SETTINGS

INDEX_SYMBOLS = {
    'SPX':   'SPX',
    'NDX':   'NDX',
    'VIX':   'VIX',
    'VIX3M': 'VIX3M',
    'VVIX':  'VVIX',
    'DXY':   '$DXY',
    'TNX':   '$TNX',
    'GLD':   'GLD',
}


OPTION_UNIVERSE = {
    'SPX': {'range': 100, 'spacing': 5.0},
    'NDX': {'range': 200, 'spacing': 5.0},
}

def get_nearest_friday(min_days=1):
    today = date.today()
    days_ahead = (4 - today.weekday()) % 7
    if days_ahead < min_days:
        days_ahead += 7
    return today + timedelta(days=days_ahead)

def fetch_thesis_snapshot(
    option_tickers=('SPX', 'NDX'),
    wait_index=3,
    wait_options=8
) -> dict:
    """
    Single-shot snapshot: fetches index prices + full options chain.
    Returns raw dict of {(symbol, quote_type): value}
    """
    pythoncom.CoInitialize()
    client = RTDClient(heartbeat_ms=SETTINGS['timing']['initial_heartbeat'])
    client.initialize()

    # ── Step 1: index quotes ──────────────────────────────────────────────
    for name, sym in INDEX_SYMBOLS.items():
        client.subscribe(QuoteType.LAST, sym)
    
    time.sleep(wait_index)
    pythoncom.PumpWaitingMessages()

    with client._value_lock:
        snapshot = {
            k: (v.value if hasattr(v, 'value') else v)
            for k, v in client._latest_values.items()
        }

    # ── Step 2: options chains ────────────────────────────────────────────
    expiry = get_nearest_friday()
    option_syms_map = {}

    for ticker in option_tickers:
        cfg = OPTION_UNIVERSE[ticker]
        # resolve spot from snapshot
        spot_key_candidates = [
            (INDEX_SYMBOLS[ticker], str(QuoteType.LAST)),
            (INDEX_SYMBOLS[ticker], 'LAST'),
        ]
        spot = None
        for k in spot_key_candidates:
            if k in snapshot:
                spot = float(snapshot[k])
                break
        if not spot:
            continue

        syms = OptionSymbolBuilder.build_symbols(
            ticker, expiry, spot, cfg['range'], cfg['spacing']
        )
        option_syms_map[ticker] = {'symbols': syms, 'spot': spot}

        for sym in syms:
            client.subscribe(QuoteType.GAMMA,    sym)
            client.subscribe(QuoteType.OPEN_INT, sym)
            client.subscribe(QuoteType.IMPL_VOL, sym)  # was IV     
            client.subscribe(QuoteType.DELTA,    sym)

    time.sleep(wait_options)
    pythoncom.PumpWaitingMessages()

    with client._value_lock:
        snapshot.update({
            k: (v.value if hasattr(v, 'value') else v)
            for k, v in client._latest_values.items()
        })

    client.Disconnect()
    pythoncom.CoUninitialize()

    return snapshot, option_syms_map
