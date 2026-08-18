import json
import numpy as np
import time
import requests
from datetime import datetime
from scipy.optimize import brentq
from scipy.stats import norm
import asyncio
import os
import sys

ANNUALIZATION_FACTOR = 365 * 24 * 3600

def extract_basic_info_from_market(m):
    expiry = datetime.fromisoformat(m["close_time"].replace("z", "+00:00"))
    expiry_ts = expiry.timestamp()
    strike = round(m["floor_strike"], 1)
    bid = float(m["yes_bid_dollars"])
    ask = float(m["yes_ask_dollars"])

    return {
        "ticker": m["ticker"],
        "strike": strike,
        "expiry_ts": expiry_ts,
        "bid": bid,
        "ask": ask,
    }



def get_greeks(
    price,  # contract price in dollars (0-1)
    spot,  # BTC spot
    strike,
    T,  # in seconds
    r=0.0,
    asian_seconds=60,
    asian_multiplier = 1/3, # 1/T * \int_0^T W_s ds has 1/3 as much total variance
):
    # Time to expiry (years)
    # asian estimation
    if T < asian_seconds:
        T = T * asian_multiplier
    else:
        # discount vol in asian time
        T = T - asian_seconds * (1 - asian_multiplier)
    T = max(T, 1)
    T = T / (ANNUALIZATION_FACTOR)  # in years

    # Discounted probability
    d2_cdf = np.clip(price * np.exp(r * T), 1e-8, 1 - 1e-8)
    d2 = norm.ppf(d2_cdf)
    log_sk = np.log(spot / strike)
    sqrtT = np.sqrt(T)

    def f(sigma):
        return (log_sk + (r - 0.5 * sigma * sigma) * T) / (sigma * sqrtT) - d2

    sigma = -1
    delta = 0
    gamma = 0
    vega = 0
    theta = 0
    try:
        sigma = brentq(f, 1e-4, 3)
    except ValueError:
        return (sigma, delta, gamma, vega, theta)

    d2_pdf = norm.pdf(d2)
    d1 = d2 + sigma * sqrtT

    delta = get_delta(spot, sigma, T, d2_pdf, r)
    gamma = get_gamma(spot, sigma, T, d2, d2_pdf, r)
    vega = get_vega(sigma, T, d1, d2_pdf, r)
    theta = get_theta(sigma, T, d1, d2_cdf, d2_pdf, r)

    return (sigma, delta, gamma, vega, theta)


def get_delta(
    spot,  # BTC spot
    sigma,
    T,  #
    d2_pdf,
    r=0.0,
):
    discount = np.exp(-r * T)
    denom = spot * sigma * T**0.5
    return discount * d2_pdf / denom


def get_gamma(
    spot,  # BTC spot
    sigma,
    T,  #
    d2,
    d2_pdf,
    r=0.0,
):
    sigma_sq_tau = sigma * T**0.5
    discount = np.exp(-r * T)
    return -discount * (d2_pdf / (spot * spot * sigma_sq_tau)) * (1 + d2 / sigma_sq_tau)


def get_vega(
    sigma,
    T,  #
    d1,
    d2_pdf,
    r=0.0,
):
    discount = np.exp(-r * T)
    return -discount * d2_pdf * d1 / sigma


def get_theta(
    sigma,
    T,  #
    d1,
    d2_pdf,
    d2_cdf,
    r=0.0,
):
    discount = np.exp(-r * T)
    th = r * discount * d2_cdf + discount * d2_pdf * (
        d1 / (2 * T) - r / (sigma * T**0.5)
    )
    return th / ANNUALIZATION_FACTOR


BASE = "https://api.elections.kalshi.com/trade-api/v2"


def get_active_btc_markets(volume_thresh, time_thresh):
    # 15 min
    params = {
        "series_ticker": "KXBTC15M",
        "status": "open",
        # "limit": 100,
    }
    now = time.time()
    r = requests.get(f"{BASE}/markets", params=params)
    r.raise_for_status()
    data = r.json()
    # now = datetime.now(timezone.utc)
    cursor = None
    markets = []

    fifteen = data["markets"][0]
    fifteen_info = extract_basic_info_from_market(fifteen)
    markets.append(fifteen_info)

    # hourly-daily
    while True:
        params = {
            "series_ticker": "KXBTCD",
            "status": "open",
        }
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE}/markets", params=params)
        r.raise_for_status()

        data = r.json()

        for m in data["markets"]:
            if float(m["volume_fp"]) < volume_thresh:
                continue
            m_info = extract_basic_info_from_market(m)
            if m_info["expiry_ts"] - now > time_thresh:
                continue

            markets.append(m_info)

        cursor = data.get("cursor")
        if not cursor:
            break
    return markets


def clear_terminal():
    """Cross-platform terminal screen clear (replacing clear_output)."""
    os.system("cls" if os.name == "nt" else "clear")


# --- Async Handlers & Subscriptions ---


def handle_cf_message(msg, state):
    """Parses BRTI messages (Synchronous since no async I/O is performed)."""
    # Note: If msg["data"] is a stringified JSON, parse it; otherwise read dict directly
    raw_data = msg.get("data")
    data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data

    avg_data = msg.get("avg_60s_data", {})

    if data and "value" in data:
        state["brti"] = float(data.get("value"))
    if avg_data and "value" in avg_data:
        state["brti_60"] = float(avg_data.get("value"))


def handle_market_message(msg, state):
    """Parses ticker updates (Synchronous)."""
    ticker = msg.get("market_ticker")
    bid = msg.get("yes_bid_dollars")
    ask = msg.get("yes_ask_dollars")

    markets_dict = state.get("markets", {})
    market_obj = markets_dict.get(ticker)

    if market_obj and bid is not None and ask is not None:
        market_obj["yes_bid"] = float(bid)
        market_obj["yes_ask"] = float(ask)

async def handle_messages(websocket, state):
    """Main WebSocket message listening loop."""
    async for message in websocket:
        js = json.loads(message)
        msg_type = js.get("type")
        msg = js.get("msg", {})

        match msg_type:
            case "cfbenchmarks_value":
                handle_cf_message(msg, state)
            case "ticker":
                handle_market_message(msg, state)
            case _:
                print("Unknown message type:", msg_type)
                print(msg) 


async def subscribe_to_cf(websocket):
    """Subscribe to CF Index updates."""
    subscription = {
        "id": 1,
        "cmd": "subscribe",
        "params": {"channels": ["cfbenchmarks_value"], "index_ids": ["BRTI"]},
    }
    await websocket.send(json.dumps(subscription))


async def subscribe_to_markets(websocket, tickers):
    """Subscribe to market ticker updates."""
    subscription = {
        "id": 2,
        "cmd": "subscribe",
        "params": {"channels": ["ticker"], "market_tickers": tickers},
    }
    await websocket.send(json.dumps(subscription))

async def subscribe_to_markets(websocket, tickers):
    """Subscribe to market ticker updates."""
    subscription = {
        "id": 3,
        "cmd": "subscribe",
        "params": {"channels": ["ticker"], "market_tickers": tickers},
    }
    await websocket.send(json.dumps(subscription))

# --- Display Summary Loop ---


async def summary(state):
    """Asynchronous loop printing terminal output every second."""
    while True:
        await asyncio.sleep(1)

        # Guard against unpopulated state on startup
        spot = state.get("brti")
        spot_avg = state.get("brti_60")
        markets = state.get("markets")

        if spot is None or spot_avg is None or not markets:
            clear_terminal()
            print("Waiting for initial WebSocket state data...")
            continue

        now = time.time()
        output_str = f"spot: {spot}\nspot_60s: {spot_avg}\n"
        output_str += "-" * 80 + "\n"

        for ticker, m in markets.items():
            # Ensure price updates have actually been received
            if "yes_ask" not in m or "yes_bid" not in m:
                continue

            ttm = max(round(m["expiry_ts"] - now), 1)
            ttm_m = round(ttm / 60, 1)

            ask = m["yes_ask"]
            bid = m["yes_bid"]
            mid = (ask + bid) / 2
            strike = m["strike"]

            # Avoid division by zero if mid-price hasn't populated properly
            if mid == 0:
                continue

            iv, delta, gamma, vega, theta = get_greeks(mid, spot, strike, ttm)

            output_str += (
                f"K: {strike:<6} | Ask: ${ask:<6.3f} | T: {ttm_m:>5.1f}m | d: {spot-strike:>7.2f} || "
                f"IV%: {iv*100:>5.3f} | "
                f"DE c: {delta*100:>7.3f} | GA .01c: {gamma*10000:>7.3f} | "
                f"VE: {vega:>7.3f} | TH c: {theta*100:>7.3f}\n"
            )

            # Update state with Greeks
            m.update(
                {
                    "distance": spot - strike,
                    "iv": iv,
                    "delta": delta,
                    "gamma": gamma,
                    "vega": vega,
                    "theta": theta,
                    "ttm": ttm,
                }
            )

        clear_terminal()
        print(output_str)


