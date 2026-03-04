import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.colors import LinearSegmentedColormap
from scipy.stats import norm
from datetime import datetime
import warnings
import matplotlib.font_manager as fm
import requests
import time
import pytz

warnings.filterwarnings('ignore')

preferred = ['Inter', 'Roboto', 'Arial', 'Segoe UI', 'DejaVu Sans']
available = [f.name for f in fm.fontManager.ttflist]
chosen = next((f for f in preferred if f in available), 'DejaVu Sans')

plt.rcParams.update({
    'font.family': chosen,
    'text.antialiased': True,
    'figure.dpi': 150,
})

WEBHOOK_URL = 'https://discordapp.com/api/webhooks/your_webhook_here'

# Minimum vmax for color normalization. Tune per ticker:
#   QQQ/SPY → 500_000_000, smaller tickers → lower.
GEX_VMAX_FLOOR = 500_000_000


def bs_gamma(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T))


def get_options_chain(ticker='QQQ', max_expiries=5):
    tk = yf.Ticker(ticker)
    hist = tk.history(period='1d', interval='1m')
    spot = float(hist['Close'].iloc[-1])
    expiries = tk.options[:max_expiries]

    records = []
    for exp in expiries:
        try:
            chain = tk.option_chain(exp)
            T = (datetime.strptime(exp, '%Y-%m-%d') - datetime.now()).days / 365
            if T <= 0:
                continue
            for opt_type, df in [('call', chain.calls), ('put', chain.puts)]:
                for _, row in df.iterrows():
                    iv = row.get('impliedVolatility', 0)
                    oi = row.get('openInterest', 0)
                    K  = row['strike']
                    if iv <= 0 or oi <= 0 or pd.isna(iv) or pd.isna(oi):
                        continue
                    if not (spot * 0.85 <= K <= spot * 1.15):
                        continue
                    gamma = bs_gamma(S=spot, K=K, T=T, r=0.045, sigma=iv)
                    sign  = 1 if opt_type == 'call' else -1
                    gex   = sign * gamma * oi * 100
                    records.append({
                        'strike': K, 'expiry': exp, 'type': opt_type,
                        'oi': oi, 'iv': iv, 'gamma': gamma, 'gex': gex
                    })
        except Exception as e:
            print(f"Error on {exp}: {e}")

    return pd.DataFrame(records), spot


def build_gex_matrix(df, spot, strike_range_pct=0.10):
    lo = spot * (1 - strike_range_pct)
    hi = spot * (1 + strike_range_pct)
    df = df[(df['strike'] >= lo) & (df['strike'] <= hi)]
    matrix = df.groupby(['strike', 'expiry'])['gex'].sum().unstack('expiry').fillna(0)
    return matrix.sort_index(ascending=False)


def fmt_gex(val):
    sign = '-' if val < 0 else ''
    av = abs(val)
    if av >= 1e6:
        return f'{sign}${av/1e6:.1f}M'
    elif av >= 1e3:
        return f'{sign}${av/1e3:.0f}K'
    return f'{sign}${av:.0f}'


def send_to_discord(image_path, webhook_url, spot, ticker='QQQ'):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(image_path, 'rb') as f:
        response = requests.post(
            webhook_url,
            data={'content': f'**{ticker} GEX** — {timestamp} — Spot: **${spot:.2f}**'},
            files={'file': (image_path, f, 'image/png')}
        )
    if response.status_code == 200:
        print(f"Sent to Discord ({timestamp})")
    else:
        print(f"Discord error: {response.status_code} — {response.text}")


def market_is_open():
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False
    return now.replace(hour=9, minute=30, second=0) <= now <= now.replace(hour=16, minute=0, second=0)


def plot_gex_heatmap(matrix, spot, ticker, fig=None, ax=None, vmax=None):
    if fig is None or ax is None:
        fig, ax = plt.subplots(figsize=(10, 13))

    if vmax is None:
        vmax = max(np.abs(matrix.values).max(), GEX_VMAX_FLOOR)

    colors = [
        (0.00, '#1a0520'),
        (0.10, '#4a0e6e'),
        (0.25, '#7b1fa2'),
        (0.42, '#1565c0'),
        (0.50, '#1e88e5'),
        (0.58, '#29b6f6'),
        (0.72, '#f9a825'),
        (0.85, '#fff176'),
        (1.00, '#ffff00'),
    ]
    cmap = LinearSegmentedColormap.from_list('gex', [(v, c) for v, c in colors])
    norm_scale = mcolors.TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax)

    im = ax.imshow(matrix.values, aspect='auto', cmap=cmap,
                   norm=norm_scale, interpolation='nearest')

    ax.set_xticks(range(len(matrix.columns)))
    ax.set_xticklabels(matrix.columns, rotation=30, ha='right', color='white', fontsize=7)
    ax.set_yticks(range(len(matrix.index)))
    ax.set_yticklabels([f'${s:.0f}' for s in matrix.index], color='white', fontsize=6)
    ax.tick_params(colors='white', length=2)
    for spine in ax.spines.values():
        spine.set_edgecolor('#333333')

    spot_idx = int(np.abs(matrix.index.values.astype(float) - spot).argmin())
    ax.axhline(y=spot_idx, color='cyan', linewidth=1.2, linestyle='--', alpha=0.9)
    ax.text(0.02, spot_idx, f' SPOT ${spot:.2f}', color='cyan', fontsize=7,
            va='center', transform=ax.get_yaxis_transform())

    col_gex = matrix.sum(axis=1).values
    for i in range(len(col_gex) - 1):
        if col_gex[i] * col_gex[i + 1] < 0:
            ax.axhline(y=i + 0.5, color='#ff8800', linewidth=1.2, linestyle=':', alpha=0.9)
            ax.text(0.02, i + 0.5, 'FLIP', color='#ff8800', fontsize=7,
                    va='bottom', transform=ax.get_yaxis_transform())
            break

    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label('Gamma Exposure (GEX)', color='white', fontsize=8)
    cbar.ax.yaxis.set_tick_params(color='white', labelsize=7)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color='white')

    for i in range(len(matrix.index)):
        for j in range(len(matrix.columns)):
            val = matrix.values[i, j]
            if val != 0:
                ax.text(j, i, fmt_gex(val), ha='center', va='center',
                        fontsize=5.5, color='white', alpha=0.95)

    ax.set_xlabel('Expiration Date', color='white', fontsize=8)
    ax.set_ylabel('Strike Price', color='white', fontsize=8)
    ax.set_title(
        f'{ticker} GEX — {datetime.now().strftime("%Y-%m-%d %H:%M")} — Spot: ${spot:.2f}',
        color='white', fontsize=10, pad=10
    )

    plt.tight_layout()
    plt.savefig('gex_heatmap.png', dpi=150, bbox_inches='tight', facecolor='#0a0a0a')


def run_loop(ticker='QQQ', interval_minutes=15):
    print(f"Starting loop — interval: {interval_minutes} min")
    while True:
        if market_is_open():
            try:
                df, spot = get_options_chain(ticker, max_expiries=5)
                matrix = build_gex_matrix(df, spot)
                plot_gex_heatmap(matrix, spot, ticker)
                send_to_discord('gex_heatmap.png', WEBHOOK_URL, spot, ticker)
            except Exception as e:
                print(f"Error: {e}")
        else:
            print("Market closed")
        time.sleep(interval_minutes * 60)


if __name__ == '__main__':
    df, spot = get_options_chain('QQQ', max_expiries=5)
    matrix = build_gex_matrix(df, spot)
    plot_gex_heatmap(matrix, spot, 'QQQ')
