# # server.py
# import asyncio
# import json
# import time
# from typing import Dict, Set, List

# import websockets
# import aiohttp
# from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import FileResponse, JSONResponse

# # --------------------------------------------------------------------------
# # Configuration
# # --------------------------------------------------------------------------
# app = FastAPI()
# app.mount("/static", StaticFiles(directory="static"), name="static")

# # caches & state
# clients: Set[WebSocket] = set()
# price_cache: Dict[str, dict] = {}      # symbol -> { price, ts }
# kline_cache: Dict[str, List[dict]] = {}  # symbol -> [kline]
# book_cache: Dict[str, dict] = {}
# stats24_cache: Dict[str, dict] = {}

# TOP_SYMBOLS: List[str] = [
#     "btcusdt","ethusdt","bnbusdt","xauusd","xrpusdt","adausdt",
#     "solusdt","dogeusdt","dotusdt","maticusdt","ltcusdt",
#     "trxusdt","bchusdt","linkusdt","uniusdt","shibusdt",
#     "avaxusdt","apeusdt","axsusdt","atomusdt","sandusdt"
# ]

# dynamic_watches: Set[str] = set()
# _subscribe_lock = asyncio.Lock()
# _symbols_changed = asyncio.Event()

# BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="
# BINANCE_24H = "https://api.binance.com/api/v3/ticker/24hr"
# BINANCE_MINI = "wss://stream.binance.com:9443/ws/!miniTicker@arr"

# # your n8n webhook (keep as-is)
# N8N_WEBHOOK = "https://idlysambar.app.n8n.cloud/webhook/c036aabf-edb7-4dfc-a86c-a82ae43cbbba"

# # --------------------------------------------------------------------------
# # Helpers
# # --------------------------------------------------------------------------
# def build_stream_url(symbols: List[str]) -> str:
#     parts = []
#     for s in sorted(symbols):
#         # only add bookTicker/kline/trade for normal symbols
#         parts.append(f"{s}@trade")
#         parts.append(f"{s}@kline_1m")
#         parts.append(f"{s}@bookTicker")
#     return BINANCE_WS_BASE + "/".join(parts)

# async def broadcast(obj: dict):
#     if not clients:
#         return
#     text = json.dumps(obj)
#     remove = []
#     for ws in list(clients):
#         try:
#             await ws.send_text(text)
#         except Exception:
#             remove.append(ws)
#     for ws in remove:
#         clients.discard(ws)

# # map to ticker expected by n8n
# def binance_to_ticker(b: str):
#     if not b: return None
#     base = b.lower().replace('usdt','').replace('usd','').upper()
#     return f"{base}/USD"

# # --------------------------------------------------------------------------
# # Periodic 24h stats & gold fetching (coingecko)
# # --------------------------------------------------------------------------
# async def fetch_24h_stats_periodically(interval: int = 30):
#     async with aiohttp.ClientSession() as session:
#         while True:
#             try:
#                 async with session.get(BINANCE_24H, timeout=20) as resp:
#                     if resp.status == 200:
#                         data = await resp.json()
#                         for item in data:
#                             s = item.get("symbol","").lower()
#                             stats24_cache[s] = {
#                                 "priceChangePercent": item.get("priceChangePercent"),
#                                 "volume": item.get("volume"),
#                                 "lastPrice": item.get("lastPrice"),
#                                 "openPrice": item.get("openPrice"),
#                                 "highPrice": item.get("highPrice"),
#                                 "lowPrice": item.get("lowPrice")
#                             }
#                         await broadcast({"type":"24h_update","ts":int(time.time()*1000)})
#             except Exception as e:
#                 print("24h fetch error:", e)
#             await asyncio.sleep(interval)

# # Coingecko gold price fetch (XAU / USD). Binance doesn't provide XAU/USD.
# async def fetch_gold_periodically(interval: int = 10):
#     url = "https://api.coingecko.com/api/v3/simple/price?ids=gold&vs_currencies=usd"
#     async with aiohttp.ClientSession() as session:
#         while True:
#             try:
#                 # Coingecko uses 'gold' endpoint? If not present, try alternative (here we attempt)
#                 async with session.get(url, timeout=10) as resp:
#                     if resp.status == 200:
#                         j = await resp.json()
#                         # coingecko may not have 'gold' — try 'tether-gold' or 'xaut' replacements if needed
#                         price = None
#                         if "gold" in j and "usd" in j["gold"]:
#                             price = j["gold"]["usd"]
#                         elif "tether-gold" in j and "usd" in j["tether-gold"]:
#                             price = j["tether-gold"]["usd"]
#                         # fallback: if price exists, store as xauusd
#                         if price is not None:
#                             symbol = "xauusd"
#                             price_cache[symbol] = {"price": str(price), "ts": int(time.time()*1000)}
#                             await broadcast({"type":"price","symbol":symbol,"data":price_cache[symbol]})
#             except Exception as e:
#                 # don't spam logs
#                 print("gold fetch error:", e)
#             await asyncio.sleep(interval)

# # --------------------------------------------------------------------------
# # Binance combined manager (trade/kline/bookTicker)
# # --------------------------------------------------------------------------
# async def binance_manager():
#     reconnect_delay = 1.0
#     ws_conn = None
#     active_snapshot = set()
#     _symbols_changed.set()

#     while True:
#         await _symbols_changed.wait()
#         _symbols_changed.clear()
#         await asyncio.sleep(0.4)

#         async with _subscribe_lock:
#             wanted = set(TOP_SYMBOLS) | set(dynamic_watches)
#         if not wanted:
#             await asyncio.sleep(1.0)
#             continue

#         # if same snapshot, keep using current ws_conn (no change)
#         if wanted == active_snapshot and ws_conn:
#             pass
#         else:
#             if ws_conn:
#                 try:
#                     await ws_conn.close()
#                 except Exception:
#                     pass
#                 ws_conn = None
#                 active_snapshot = set()

#         url = build_stream_url(list(wanted))
#         try:
#             print("Connecting to Binance combined stream for:", sorted(wanted)[:10], "...")
#             ws_conn = await websockets.connect(url, ping_interval=20, ping_timeout=10)
#             print("Connected to Binance combined stream.")
#             reconnect_delay = 1.0
#             active_snapshot = set(wanted)

#             while True:
#                 if _symbols_changed.is_set():
#                     print("Symbols changed, reconnecting combined stream...")
#                     break
#                 try:
#                     msg = await asyncio.wait_for(ws_conn.recv(), timeout=30)
#                 except asyncio.TimeoutError:
#                     continue
#                 except websockets.ConnectionClosed as e:
#                     print("binance closed:", e)
#                     break
#                 except Exception as e:
#                     print("recv error:", e)
#                     break

#                 try:
#                     obj = json.loads(msg)
#                     data = obj.get("data", {})
#                     ev = data.get("e")

#                     # Trade
#                     if ev == "trade":
#                         sym = data.get("s","").lower()
#                         price = data.get("p")
#                         qty = data.get("q")
#                         ts = data.get("E") or int(time.time()*1000)
#                         price_cache[sym] = {"price": price, "ts": ts}
#                         await broadcast({"type":"trade","symbol":sym,"price":price,"qty":qty,"ts":ts})

#                     # Kline
#                     elif ev == "kline" or "k" in data:
#                         k = data.get("k") or data
#                         sym = (k.get("s") or data.get("s") or obj.get("stream","")).lower()
#                         kl = {"t": k.get("t"), "T": k.get("T"), "o": k.get("o"), "h": k.get("h"),
#                               "l": k.get("l"), "c": k.get("c"), "v": k.get("v"), "x": k.get("x")}
#                         lst = kline_cache.setdefault(sym, [])
#                         lst.append(kl)
#                         if len(lst) > 240:
#                             del lst[:-240]
#                         await broadcast({"type":"kline","symbol":sym,"k":kl})

#                     # BookTicker
#                     elif ev == "bookTicker":
#                         sym = data.get("s","").lower()
#                         book_cache[sym] = {"bid": data.get("b"), "bidQty": data.get("B"),
#                                            "ask": data.get("a"), "askQty": data.get("A")}
#                         await broadcast({"type":"bookTicker","symbol":sym,"bid":data.get("b"),"ask":data.get("a")})

#                 except Exception as e:
#                     print("parse error:", e)
#                     continue

#         except Exception as e:
#             print("connect error:", e)
#             await asyncio.sleep(reconnect_delay)
#             reconnect_delay = min(reconnect_delay * 1.5, 30)
#             continue

# # --------------------------------------------------------------------------
# # miniTicker manager - frequent lightweight lastPrice updates
# # --------------------------------------------------------------------------
# async def mini_ticker_manager():
#     reconnect_delay = 1.0
#     while True:
#         try:
#             print("Connecting to Binance miniTicker stream...")
#             async with websockets.connect(BINANCE_MINI, ping_interval=20, ping_timeout=10) as ws:
#                 print("Connected to miniTicker stream.")
#                 reconnect_delay = 1.0
#                 async for msg in ws:
#                     try:
#                         obj = json.loads(msg)
#                     except Exception:
#                         continue
#                     # normalize to entries list
#                     items = None
#                     if isinstance(obj, dict) and 'data' in obj:
#                         data = obj['data']
#                         items = data if isinstance(data, list) else [data]
#                     elif isinstance(obj, list):
#                         items = obj
#                     elif isinstance(obj, dict):
#                         items = [obj]
#                     else:
#                         items = []
#                     for entry in items:
#                         s = entry.get('s') or entry.get('symbol') or entry.get('S')
#                         if not s: continue
#                         symbol = s.lower()
#                         last = entry.get('c') or entry.get('close') or entry.get('lastPrice')
#                         if last is None: continue
#                         # update cache and broadcast
#                         price_cache[symbol] = {"price": str(last), "ts": int(time.time()*1000)}
#                         await broadcast({"type":"miniTicker","symbol":symbol,"lastPrice": str(last)})
#         except Exception as e:
#             print("miniTicker connection error:", e)
#             await asyncio.sleep(reconnect_delay)
#             reconnect_delay = min(reconnect_delay * 1.5, 30)

# # --------------------------------------------------------------------------
# # HTTP endpoints
# # --------------------------------------------------------------------------
# @app.get("/price_check")
# async def price_check(symbol: str = Query(...)):
#     symbol = symbol.upper()
#     url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
#     timeout = aiohttp.ClientTimeout(total=6)
#     async with aiohttp.ClientSession(timeout=timeout) as session:
#         try:
#             async with session.get(url) as resp:
#                 if resp.status == 200:
#                     data = await resp.json()
#                     return {"ok": True, "data": data}
#                 return {"ok": False, "status": resp.status}
#         except Exception as e:
#             return {"ok": False, "error": str(e)}

# @app.get("/api/price_for/{symbol}")
# async def price_for(symbol: str):
#     s = symbol.lower()
#     data = price_cache.get(s)
#     if data and "price" in data:
#         return {"ok": True, "price": data["price"], "ts": data.get("ts")}
#     # fallback to REST quick call (safe)
#     url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
#     timeout = aiohttp.ClientTimeout(total=6)
#     async with aiohttp.ClientSession(timeout=timeout) as session:
#         try:
#             async with session.get(url) as resp:
#                 if resp.status == 200:
#                     j = await resp.json()
#                     return {"ok": True, "price": j.get("price")}
#         except Exception:
#             pass
#     return {"ok": False}

# @app.post("/analyze")
# async def analyze(req: Request):
#     body = await req.json()
#     bin_sym = (body.get("binanceSymbol") or body.get("symbol") or "").upper()
#     ticker = binance_to_ticker(bin_sym)
#     current_price = None
#     if bin_sym.lower() in price_cache:
#         current_price = price_cache[bin_sym.lower()].get("price")
#     payload = {"ticker": ticker}
#     async with aiohttp.ClientSession() as session:
#         try:
#             async with session.post(N8N_WEBHOOK, json=payload, timeout=20) as r:
#                 text = await r.text()
#                 try:
#                     j = await r.json()
#                 except Exception:
#                     j = {"raw": text}
#                 resp = {"ok": True, "source": "n8n", "n8n": j}
#                 if current_price is not None:
#                     resp["currentPrice"] = current_price
#                 return JSONResponse(resp)
#         except Exception as e:
#             return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

# @app.get("/snapshot")
# async def snapshot():
#     return {
#         "prices": price_cache,
#         "klines": {s: kline_cache.get(s, [])[-60:] for s in TOP_SYMBOLS},
#         "book": book_cache,
#         "stats24": stats24_cache,
#     }

# @app.get("/")
# async def root():
#     return FileResponse("static/index.html")

# # --------------------------------------------------------------------------
# # Websocket endpoint for clients
# # --------------------------------------------------------------------------
# @app.websocket("/ws")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     clients.add(websocket)
#     print("Client connected:", websocket.client)
#     try:
#         snapshot = {
#             "type":"snapshot",
#             "prices": {s: price_cache.get(s) for s in TOP_SYMBOLS},
#             "klines": {s: kline_cache.get(s, [])[-60:] for s in TOP_SYMBOLS},
#             "book": {s: book_cache.get(s) for s in TOP_SYMBOLS},
#             "stats24": {s: stats24_cache.get(s) for s in TOP_SYMBOLS}
#         }
#         await websocket.send_text(json.dumps(snapshot))

#         while True:
#             text = await websocket.receive_text()
#             try:
#                 msg = json.loads(text)
#             except:
#                 await websocket.send_text(json.dumps({"error":"invalid json"}))
#                 continue
#             action = msg.get("action")
#             symbol = (msg.get("symbol") or "").lower().strip()
#             if not symbol:
#                 await websocket.send_text(json.dumps({"error":"missing symbol"}))
#                 continue
#             if action == "watch":
#                 async with _subscribe_lock:
#                     if symbol not in TOP_SYMBOLS and symbol not in dynamic_watches:
#                         dynamic_watches.add(symbol)
#                         _symbols_changed.set()
#                 if symbol in price_cache:
#                     await websocket.send_text(json.dumps({"type":"price","symbol":symbol,"data":price_cache[symbol]}))
#             elif action == "unwatch":
#                 async with _subscribe_lock:
#                     dynamic_watches.discard(symbol)
#                     _symbols_changed.set()
#                 await websocket.send_text(json.dumps({"ok":True,"unwatched":symbol}))
#             else:
#                 await websocket.send_text(json.dumps({"error":"unknown action"}))

#     except WebSocketDisconnect:
#         print("Client disconnected:", websocket.client)
#     except Exception as e:
#         print("WS error:", e)
#     finally:
#         clients.discard(websocket)

# # --------------------------------------------------------------------------
# # Startup tasks
# # --------------------------------------------------------------------------
# @app.on_event("startup")
# async def startup_event():
#     asyncio.create_task(binance_manager())
#     asyncio.create_task(fetch_24h_stats_periodically(30))
#     asyncio.create_task(mini_ticker_manager())
#     asyncio.create_task(fetch_gold_periodically(15))
#     print("Startup: binance_manager, 24h fetcher, mini_ticker, gold fetcher started")
# server.py
# server.py
# server.py
import asyncio
import json
import time
from typing import Dict, Set, List

import websockets
import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

# -------------------------------------------------------------------------
# FastAPI + static files
# -------------------------------------------------------------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# -------------------------------------------------------------------------
# Global state
# -------------------------------------------------------------------------
clients: Set[WebSocket] = set()

price_cache: Dict[str, dict] = {}     # latest trade/miniTicker price { price, ts }
kline_cache: Dict[str, List[dict]] = {}  # list of recent 1m klines
book_cache: Dict[str, dict] = {}
stats24_cache: Dict[str, dict] = {}

# add xauusd (gold) into TOP list (we'll fetch gold via CoinGecko)
TOP_SYMBOLS: List[str] = [
    "btcusdt","ethusdt","bnbusdt","xauusd","xrpusdt","adausdt",
    "solusdt","dogeusdt","dotusdt","maticusdt","ltcusdt",
    "trxusdt","bchusdt","linkusdt","uniusdt","shibusdt",
    "avaxusdt","apeusdt","axsusdt","atomusdt","sandusdt"
]

dynamic_watches: Set[str] = set()
_subscribe_lock = asyncio.Lock()
_symbols_changed = asyncio.Event()

BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="
BINANCE_24H = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_MINI = "wss://stream.binance.com:9443/ws/!miniTicker@arr"

# Replace this with your n8n webhook
N8N_WEBHOOK = "https://syndney123.app.n8n.cloud/webhook/c036aabf-edb7-4dfc-a86c-a82ae43cbbba"

# -------------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------------
def build_stream_url(symbols: List[str]) -> str:
    parts = []
    for s in sorted(symbols):
        parts.append(f"{s}@trade")
        parts.append(f"{s}@kline_1m")
        parts.append(f"{s}@bookTicker")
    return BINANCE_WS_BASE + "/".join(parts)

async def broadcast(obj: dict):
    if not clients:
        return
    text = json.dumps(obj)
    remove = []
    for ws in list(clients):
        try:
            await ws.send_text(text)
        except Exception:
            remove.append(ws)
    for ws in remove:
        clients.discard(ws)

# -------------------------------------------------------------------------
# Periodic fetchers
# -------------------------------------------------------------------------
async def fetch_24h_stats_periodically(interval: int = 30):
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(BINANCE_24H, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data:
                            s = item.get("symbol","").lower()
                            stats24_cache[s] = {
                                "priceChangePercent": item.get("priceChangePercent"),
                                "volume": item.get("volume"),
                                "lastPrice": item.get("lastPrice"),
                                "openPrice": item.get("openPrice"),
                                "highPrice": item.get("highPrice"),
                                "lowPrice": item.get("lowPrice")
                            }
                        await broadcast({"type":"24h_update","ts":int(time.time()*1000)})
            except Exception as e:
                print("24h fetch error:", e)
            await asyncio.sleep(interval)

async def fetch_coingecko_gold_periodically(interval: int = 5):
    """Fetch gold price from CoinGecko and store as xauusd in price_cache."""
    # CoinGecko simple price endpoint for many ids; we'll use 'tether-gold' (XAUT) fallback
    CG_URLS = [
        # try a few ids if available; most reliable is "tether-gold" or "gold"
        "https://api.coingecko.com/api/v3/simple/price?ids=tether-gold&vs_currencies=usd",
        "https://api.coingecko.com/api/v3/simple/price?ids=gold&vs_currencies=usd",
    ]
    
    async with aiohttp.ClientSession() as session:
        while True:
            price = None
            for url in CG_URLS:
                try:
                    async with session.get(url, timeout=6) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # check keys
                            if "tether-gold" in data and data["tether-gold"].get("usd") is not None:
                                price = data["tether-gold"]["usd"]
                                break
                            if "gold" in data and data["gold"].get("usd") is not None:
                                price = data["gold"]["usd"]
                                break
                except Exception:
                    continue
            if price is not None:
                # normalize mapping to xauusd (lower-case)
                s = "xauusd"
                price_cache[s] = {"price": str(price), "ts": int(time.time()*1000)}
                # broadcast to clients like a miniTicker update
                await broadcast({"type":"miniTicker","symbol":s,"lastPrice": str(price)})
            await asyncio.sleep(interval)
            

# -------------------------------------------------------------------------
# Binance managers
# -------------------------------------------------------------------------
async def binance_manager():
    reconnect_delay = 1.0
    ws_conn = None
    active_snapshot = set()

    _symbols_changed.set()
    while True:
        await _symbols_changed.wait()
        _symbols_changed.clear()
        await asyncio.sleep(0.5)

        async with _subscribe_lock:
            wanted = set(TOP_SYMBOLS) | set(dynamic_watches)
        if not wanted:
            await asyncio.sleep(1.0)
            continue

        if wanted == active_snapshot and ws_conn:
            pass
        else:
            if ws_conn:
                try:
                    await ws_conn.close()
                except Exception:
                    pass
                ws_conn = None
                active_snapshot = set()

        url = build_stream_url(list(wanted))
        try:
            print("Connecting to Binance combined stream for:", sorted(wanted)[:8], "...")
            ws_conn = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            reconnect_delay = 1.0
            active_snapshot = set(wanted)

            while True:
                if _symbols_changed.is_set():
                    print("Symbols changed -> reconnecting combined stream")
                    break
                try:
                    msg = await asyncio.wait_for(ws_conn.recv(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed as e:
                    print("binance closed:", e)
                    break
                except Exception as e:
                    print("recv error:", e)
                    break

                try:
                    obj = json.loads(msg)
                    data = obj.get("data", {})
                    ev = data.get("e")

                    # trade
                    if ev == "trade":
                        sym = data.get("s","").lower()
                        price = data.get("p")
                        qty = data.get("q")
                        ts = data.get("E") or int(time.time()*1000)
                        price_cache[sym] = {"price": price, "ts": ts}
                        await broadcast({"type":"trade","symbol":sym,"price":price,"qty":qty,"ts":ts})
                    # kline
                    elif ev == "kline" or "k" in data:
                        k = data.get("k") or data
                        sym = (k.get("s") or data.get("s") or obj.get("stream","")).lower()
                        kl = {"t": k.get("t"), "T": k.get("T"), "o": k.get("o"), "h": k.get("h"),
                              "l": k.get("l"), "c": k.get("c"), "v": k.get("v"), "x": k.get("x")}
                        lst = kline_cache.setdefault(sym, [])
                        lst.append(kl)
                        if len(lst) > 240:
                            del lst[:-240]
                        await broadcast({"type":"kline","symbol":sym,"k":kl})
                    # bookTicker
                    elif ev == "bookTicker":
                        sym = data.get("s","").lower()
                        book_cache[sym] = {"bid": data.get("b"), "bidQty": data.get("B"),
                                           "ask": data.get("a"), "askQty": data.get("A")}
                        await broadcast({"type":"bookTicker","symbol":sym,"bid":data.get("b"),"ask":data.get("a")})
                except Exception as e:
                    print("parse error:", e)
                    continue
        except Exception as e:
            print("connect error:", e)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 30)
            continue

async def mini_ticker_manager():
    reconnect_delay = 1.0
    while True:
        try:
            print("Connecting to Binance miniTicker stream...")
            async with websockets.connect(BINANCE_MINI, ping_interval=20, ping_timeout=10) as ws:
                print("Connected to miniTicker stream.")
                reconnect_delay = 1.0
                async for msg in ws:
                    try:
                        obj = json.loads(msg)
                    except Exception:
                        continue

                    items = []
                    if isinstance(obj, dict) and 'data' in obj:
                        data = obj['data']
                        items = data if isinstance(data, list) else [data]
                    elif isinstance(obj, list):
                        items = obj
                    elif isinstance(obj, dict):
                        items = [obj]

                    for entry in items:
                        s = entry.get('s') or entry.get('symbol') or entry.get('S')
                        if not s:
                            continue
                        symbol = s.lower()
                        last = entry.get('c') or entry.get('close') or entry.get('lastPrice')
                        price_cache[symbol] = {"price": last, "ts": int(time.time()*1000)}
                        await broadcast({"type":"miniTicker","symbol":symbol,"lastPrice": last})
        except Exception as e:
            print("miniTicker connection error:", e)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 30)

# REST polling fallback for live prices (use when Binance WS blocked)
import math
async def rest_polling_fallback(interval: float = 2.0):
    """Poll Binance REST API for TOP_SYMBOLS every `interval` seconds.
       Keeps price_cache populated and broadcasts miniTicker events.
    """
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                tasks = []
                # group into one REST request per symbol (Binance doesn't support batch price for arbitrary symbols),
                # but to reduce requests you can call /api/v3/ticker/price for each symbol in a short loop
                for s in list(TOP_SYMBOLS):
                    # ensure symbol is uppercase for REST
                    symbol = s.upper()
                    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                    tasks.append(session.get(url))
                # execute requests in parallel
                responses = await asyncio.gather(*tasks, return_exceptions=True)
                ts = int(time.time() * 1000)
                for idx, resp in enumerate(responses):
                    if isinstance(resp, Exception):
                        continue
                    try:
                        async with resp:
                            if resp.status == 200:
                                j = await resp.json()
                                sym = (j.get("symbol") or "").lower()
                                price = j.get("price")
                                if sym and price is not None:
                                    price_cache[sym] = {"price": str(price), "ts": ts}
                                    # broadcast a miniTicker-like update
                                    await broadcast({"type":"miniTicker","symbol":sym,"lastPrice": str(price)})
                    except Exception:
                        continue
            except Exception as e:
                # don't spam logs
                print("rest polling error:", e)
            # jitter sleep to avoid sync storms
            await asyncio.sleep(interval + (0.2 * (math.sin(time.time()) + 1)))

# -------------------------------------------------------------------------
# HTTP endpoints
# -------------------------------------------------------------------------
@app.get("/price_check")
async def price_check(symbol: str = Query(...)):
    symbol = symbol.upper()
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"ok": True, "data": data}
                return {"ok": False, "status": resp.status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

@app.get("/snapshot")
async def snapshot():
    return {
        "prices": price_cache,
        "klines": {s: kline_cache.get(s, [])[-60:] for s in TOP_SYMBOLS},
        "book": book_cache,
        "stats24": stats24_cache,
    }

@app.get("/api/price_for/{symbol}")
async def price_for(symbol: str):
    s = symbol.lower()
    data = price_cache.get(s)
    if data and "price" in data:
        return {"ok": True, "price": data["price"], "ts": data.get("ts")}
    return {"ok": False}

# @app.post("/analyze")
# async def analyze(req: Request):
#     """
#     Proxy to n8n webhook. Expects JSON body like { binanceSymbol: 'BTCUSDT' }.
#     Returns consistent JSON:
#       {"ok": True, "source":"n8n", "n8n": <object>, "currentPrice": <str or number>}
#     """
#     body = await req.json()
#     bin_sym = (body.get("binanceSymbol") or body.get("symbol") or "").upper()
#     if not bin_sym:
#         return JSONResponse({"ok": False, "error": "missing symbol"}, status_code=400)

#     # Read server-side cache current price if available
#     current_price = None
#     if bin_sym.lower() in price_cache:
#         current_price = price_cache[bin_sym.lower()].get("price")

#     # Prepare payload for n8n (they expect ticker like BTC/USD)
#     def binanceToTickerServer(b):
#         base = b.lower().replace('usdt','').upper()
#         return f"{base}/USD"
#     ticker = binanceToTickerServer(bin_sym)

#     payload = {"ticker": ticker}

#     async with aiohttp.ClientSession() as session:
#         try:
#             async with session.post(N8N_WEBHOOK, json=payload, timeout=20) as r:
#                 text = await r.text()
#                 try:
#                     j = await r.json()
#                 except Exception:
#                     # sometimes n8n returns raw text/JSON-string; keep raw
#                     j = {"raw": text}
#                 resp = {"ok": True, "source": "n8n", "n8n": j}
#                 if current_price is not None:
#                     resp["currentPrice"] = current_price
#                 return JSONResponse(resp)
#         except Exception as e:
#             return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
@app.post("/analyze")
async def analyze(req: Request):
    """
    Proxy to n8n webhook. Expects JSON body like { binanceSymbol: 'BTCUSDT' }.
    Returns consistent JSON:
      {"ok": True, "source":"n8n", "n8n": <object>, "currentPrice": <str or number>}
    """
    body = await req.json()
    bin_sym = (body.get("binanceSymbol") or body.get("symbol") or "").upper()
    if not bin_sym:
        return JSONResponse({"ok": False, "error": "missing symbol"}, status_code=400)

    # Read server-side cache current price if available
    current_price = None
    if bin_sym.lower() in price_cache:
        current_price = price_cache[bin_sym.lower()].get("price")

    # Prepare payload for n8n (they expect ticker like BTC/USD)
    def binanceToTickerServer(b):
        if not b:
            return None
        b_lower = b.lower()
        
        # Special case for gold
        if b_lower in ['xauusd', 'xau', 'gold']:
            return 'XAU/USD'
        
        # For crypto: remove 'usdt' and add '/USD'
        base = b_lower.replace('usdt', '').upper()
        return f"{base}/USD"
    
    ticker = binanceToTickerServer(bin_sym)

    payload = {"ticker": ticker}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(N8N_WEBHOOK, json=payload, timeout=20) as r:
                text = await r.text()
                try:
                    j = await r.json()
                except Exception:
                    j = {"raw": text}
                resp = {"ok": True, "source": "n8n", "n8n": j}
                if current_price is not None:
                    resp["currentPrice"] = current_price
                return JSONResponse(resp)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
@app.get("/")
async def root():
    return FileResponse("static/index.html")

# -------------------------------------------------------------------------
# WebSocket endpoint
# -------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    print("Client connected:", websocket.client)
    try:
        # initial snapshot for TOP_SYMBOLS
        snapshot = {
            "type": "snapshot",
            "prices": {s: price_cache.get(s) for s in TOP_SYMBOLS},
            "klines": {s: kline_cache.get(s, [])[-60:] for s in TOP_SYMBOLS},
            "book": {s: book_cache.get(s) for s in TOP_SYMBOLS},
            "stats24": {s: stats24_cache.get(s) for s in TOP_SYMBOLS},
        }
        await websocket.send_text(json.dumps(snapshot))

        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
            except:
                await websocket.send_text(json.dumps({"error":"invalid json"}))
                continue
            action = msg.get("action")
            symbol = (msg.get("symbol") or "").lower().strip()
            if not symbol:
                await websocket.send_text(json.dumps({"error":"missing symbol"}))
                continue
            if action == "watch":
                async with _subscribe_lock:
                    if symbol not in TOP_SYMBOLS and symbol not in dynamic_watches:
                        dynamic_watches.add(symbol)
                        _symbols_changed.set()
                # send immediate cached price if exists
                if symbol in price_cache:
                    await websocket.send_text(json.dumps({"type":"price","symbol":symbol,"data":price_cache[symbol]}))
                await websocket.send_text(json.dumps({"ok": True, "watched": symbol}))
            elif action == "unwatch":
                async with _subscribe_lock:
                    dynamic_watches.discard(symbol)
                    _symbols_changed.set()
                await websocket.send_text(json.dumps({"ok":True,"unwatched":symbol}))
            else:
                await websocket.send_text(json.dumps({"error":"unknown action"}))
    except WebSocketDisconnect:
        print("Client disconnected:", websocket.client)
    except Exception as e:
        print("WS error:", e)
        
    finally:
        clients.discard(websocket)

# -------------------------------------------------------------------------
# Startup: create background tasks (only once)
# -------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(binance_manager())
    asyncio.create_task(fetch_24h_stats_periodically(30))
    asyncio.create_task(mini_ticker_manager())
    asyncio.create_task(fetch_coingecko_gold_periodically(5))
    # start REST fallback alongside mini_ticker_manager
    asyncio.create_task(rest_polling_fallback(2.0))
    print("Startup: binance_manager, 24h fetcher, mini_ticker, coingecko, rest fallback started")

