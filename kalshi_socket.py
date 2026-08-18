from kalshi_auth import load_private_key_from_file, sign_pss_text
from kalshi_utils import (
    subscribe_to_markets,
    subscribe_to_cf,
    handle_messages,
    summary,
    get_active_btc_markets,
)
from datetime import datetime
import os
import dotenv
import asyncio
import websockets
import sys


async def connect(ws_url, headers, tickers, state):
    # Use an explicit reconnect loop for production reliability
    while True:
        try:
            async with websockets.connect(
                ws_url, additional_headers=headers
            ) as websocket:
                print("Connected to Kalshi WebSocket")

                await subscribe_to_cf(websocket)
                await subscribe_to_markets(websocket, tickers)

                await asyncio.gather(
                    handle_messages(websocket, state), summary(state)
                )

        except (websockets.ConnectionClosedError, websockets.ConnectionClosedOK) as e:
            print(f"Connection closed ({e}). Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Unexpected error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    dotenv.load_dotenv()
    api_key = os.getenv("KALSHI_API_KEY_ID")

    volume_thresh = 10000
    time_thresh = 3601

    if len(sys.argv) >= 2:
        volume_thresh = int(sys.argv[1])
    if len(sys.argv) >= 3:
        time_thresh = int(sys.argv[2])

    # WebSocket URL
    ws_url = "wss://external-api-ws.kalshi.com/trade-api/ws/v2" 

    print('Loading markets...')
    markets = get_active_btc_markets(volume_thresh, time_thresh)
    print(f"Loaded {len(markets)} markets")
    state = {
        "brti_60": None,
        "brti": None,
        "markets": {
            m['ticker'] : {
                'ticker': m['ticker'],
                'strike': m['strike'],
                'yes_bid' : m['bid'],
                'yes_ask' : m['ask'],
                'distance': 0,
                'ttm': -1,
                'iv': -1,
                'delta': 0,
                'gamma': 0,
                'vega': 0,
                'theta': 0,
                'expiry_ts': m['expiry_ts'],
            }   for m in markets
        }
    }

    tickers = [m['ticker'] for m in markets]

    print('Signing headers...')
    private_key = load_private_key_from_file('.key')

    method = "GET"
    base_url = '"wss://external-api-ws.kalshi.com'

    current_time = datetime.now()
    timestamp = current_time.timestamp()
    current_time_milliseconds = int(timestamp * 1000)
    timestampt_str = str(current_time_milliseconds)
    msg_string = timestampt_str + method + "/trade-api/ws/v2"
    sig = sign_pss_text(private_key, msg_string)

    headers = {
        'KALSHI-ACCESS-KEY': api_key,
        'KALSHI-ACCESS-SIGNATURE': sig,
        'KALSHI-ACCESS-TIMESTAMP': timestampt_str
    }

    try:
        asyncio.run(connect(ws_url, headers, tickers, state))
    except KeyboardInterrupt:
        print("\nDisconnected gracefully.")
