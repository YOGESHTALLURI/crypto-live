
# import asyncio
# import json
# from typing import Dict, Set, List

# import websockets
# from fastapi import FastAPI, WebSocket, WebSocketDisconnect
# from fastapi.staticfiles import StaticFiles
# from fastapi.responses import FileResponse

# app = FastAPI()
# app.mount("/static", StaticFiles(directory="static"), name="static")

# # All connected clients
# clients: Set[WebSocket] = set()

# # Price cache: symbol -> { price, ts }
# price_cache: Dict[str, dict] = {}

# # Top 20 symbols (lowercase, Binance pair format)
# TOP_SYMBOLS: List[str] = [
#     "btcusdt","ethusdt","bnbusdt","xrpusdt","adausdt",
#     "solusdt","dogeusdt","dotusdt","maticusdt","ltcusdt",
#     "trxusdt","bchusdt","linkusdt","uniusdt","shibusdt",
#     "avaxusdt","apeusdt","axsusdt","atomusdt","sandusdt"
# ]

# # Dynamic set for additional watched symbols requested by users
# dynamic_watches: Set[str] = set()
# _subscribe_lock = asyncio.Lock()
# _symbols_changed = asyncio.Event()

# BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="

# def build_stream_url(symbols: List[str]) -> str:
#     streams = "/".join(f"{s}@trade" for s in sorted(symbols))
#     return BINANCE_WS_BASE + streams

# async def broadcast_to_all(symbol: str, payload: dict):
#     if not clients:
#         return
#     text = json.dumps({"type": "price", "symbol": symbol, "data": payload})
#     remove = []
#     for ws in list(clients):
#         try:
#             await ws.send_text(text)
#         except Exception:
#             remove.append(ws)
#     for ws in remove:
#         clients.discard(ws)

# async def binance_manager():
#     """
#     Maintains one Binance combined connection covering TOP_SYMBOLS + dynamic_watches.
#     Reconnects when dynamic_watches changes. Debounces rapid changes.
#     """
#     reconnect_delay = 1.0
#     ws_conn = None
#     active_symbols_snapshot = set()

#     while True:
#         # Wait until something triggers a change (startup triggers by being set below)
#         await _symbols_changed.wait()
#         _symbols_changed.clear()

#         # debounce a bit to batch multiple changes
#         await asyncio.sleep(0.6)

#         async with _subscribe_lock:
#             wanted = set(TOP_SYMBOLS) | set(dynamic_watches)
#         if wanted == active_symbols_snapshot and ws_conn:
#             # nothing to do
#             continue

#         # close existing connection if any
#         if ws_conn:
#             try:
#                 await ws_conn.close()
#             except Exception:
#                 pass
#             ws_conn = None
#             active_symbols_snapshot = set()

#         if not wanted:
#             # nothing to connect to; wait for next change
#             continue

#         url = build_stream_url(list(wanted))
#         try:
#             print("Connecting to Binance for symbols:", sorted(wanted))
#             ws_conn = await websockets.connect(url, ping_interval=20, ping_timeout=10)
#             print("Connected to Binance combined stream.")
#             reconnect_delay = 1.0
#             active_symbols_snapshot = set(wanted)

#             # receive loop
#             while True:
#                 # if symbols change, break out to reconnect
#                 if _symbols_changed.is_set():
#                     print("Symbol set changed, reconnecting to updated stream...")
#                     break
#                 try:
#                     msg = await asyncio.wait_for(ws_conn.recv(), timeout=30)
#                 except asyncio.TimeoutError:
#                     # timeout: loop continue (websockets lib does pings under the hood)
#                     continue
#                 except websockets.ConnectionClosed as e:
#                     print("Binance WS closed:", e)
#                     break
#                 except Exception as e:
#                     print("Binance receive error:", e)
#                     break

#                 # parse the combined-stream message
#                 try:
#                     obj = json.loads(msg)
#                     data = obj.get("data", {})
#                     sym = data.get("s", "").lower()
#                     price = data.get("p")
#                     ts = data.get("E")
#                     if not sym or price is None:
#                         continue
#                     prev = price_cache.get(sym, {}).get("price")
#                     if prev != price:
#                         price_cache[sym] = {"price": price, "ts": ts}
#                         await broadcast_to_all(sym, {"price": price, "ts": ts})
#                 except Exception as e:
#                     print("Error parsing Binance msg:", e)
#                     continue

#         except Exception as e:
#             print("Error connecting to Binance:", e)
#             await asyncio.sleep(reconnect_delay)
#             reconnect_delay = min(reconnect_delay * 1.5, 30)
#             continue

# @app.on_event("startup")
# async def startup_event():
#     # ensure the manager sees an initial change to connect to TOP_SYMBOLS
#     asyncio.create_task(binance_manager())
#     async with _subscribe_lock:
#         # mark initial change so binance_manager will connect
#         _symbols_changed.set()
#     print("Binance manager started for Top symbols.")

# # WebSocket endpoint for browsers
# @app.websocket("/ws")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     clients.add(websocket)
#     print("Client connected:", websocket.client)
#     try:
#         # send initial snapshot for top symbols and any cached dynamic symbols
#         snapshot = {s: price_cache.get(s) for s in TOP_SYMBOLS}
#         await websocket.send_text(json.dumps({"type": "snapshot", "data": snapshot}))

#         # keep listening for client messages (watch/unwatch)
#         while True:
#             text = await websocket.receive_text()
#             # expected messages: { action: "watch"|"unwatch", symbol: "btcusdt" }
#             try:
#                 msg = json.loads(text)
#             except:
#                 await websocket.send_text(json.dumps({"error": "invalid json"}))
#                 continue
#             action = msg.get("action")
#             symbol = (msg.get("symbol") or "").lower().strip()
#             if not symbol:
#                 await websocket.send_text(json.dumps({"error": "missing symbol"}))
#                 continue

#             if action == "watch":
#                 async with _subscribe_lock:
#                     if symbol not in dynamic_watches and symbol not in TOP_SYMBOLS:
#                         dynamic_watches.add(symbol)
#                         _symbols_changed.set()
#                 # send immediate cached value if available
#                 if symbol in price_cache:
#                     await websocket.send_text(json.dumps({"type": "price", "symbol": symbol, "data": price_cache[symbol]}))
#             elif action == "unwatch":
#                 async with _subscribe_lock:
#                     if symbol in dynamic_watches:
#                         # check if any other client still needs it
#                         still_needed = False
#                         # We keep it simple: remove and set change (cleanup not strictly perfect in race but fine for dev)
#                         dynamic_watches.discard(symbol)
#                         _symbols_changed.set()
#                 await websocket.send_text(json.dumps({"ok": True, "unwatched": symbol}))
#             else:
#                 await websocket.send_text(json.dumps({"error": "unknown action"}))

#     except WebSocketDisconnect:
#         print("Client disconnected:", websocket.client)
#     except Exception as e:
#         print("WS error:", e)
#     finally:
#         clients.discard(websocket)

# # snapshot endpoint for debugging
# @app.get("/snapshot")
# async def snapshot():
#     return {"top": {s: price_cache.get(s) for s in TOP_SYMBOLS}, "dynamic": list(dynamic_watches)}

# # serve index
# @app.get("/")
# async def root():
#     return FileResponse("static/index.html")

# server.py (only the full file for clarity)
import asyncio
import json
from typing import Dict, Set, List

import websockets
import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

clients: Set[WebSocket] = set()
price_cache: Dict[str, dict] = {}

TOP_SYMBOLS: List[str] = [
    "btcusdt","ethusdt","bnbusdt","xrpusdt","adausdt",
    "solusdt","dogeusdt","dotusdt","maticusdt","ltcusdt",
    "trxusdt","bchusdt","linkusdt","uniusdt","shibusdt",
    "avaxusdt","apeusdt","axsusdt","atomusdt","sandusdt"
]

# dynamic watches
dynamic_watches: Set[str] = set()
_subscribe_lock = asyncio.Lock()
_symbols_changed = asyncio.Event()

# valid symbol set from Binance
VALID_SYMBOLS: Set[str] = set()

BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="
BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

def build_stream_url(symbols: List[str]) -> str:
    streams = "/".join(f"{s}@trade" for s in sorted(symbols))
    return BINANCE_WS_BASE + streams

async def fetch_valid_symbols():
    global VALID_SYMBOLS
    print("Fetching Binance exchangeInfo to build valid symbol list...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BINANCE_EXCHANGE_INFO, timeout=20) as resp:
                data = await resp.json()
                syms = data.get("symbols", [])
                VALID_SYMBOLS = set(s.get("symbol","").lower() for s in syms if s.get("status") == "TRADING")
                print(f"Loaded {len(VALID_SYMBOLS)} valid symbols from Binance.")
    except Exception as e:
        print("Warning: could not fetch exchangeInfo:", e)
        VALID_SYMBOLS = set()  # fallback — treat everything as invalid

async def broadcast_to_all(symbol: str, payload: dict):
    if not clients:
        return
    text = json.dumps({"type": "price", "symbol": symbol, "data": payload})
    remove = []
    for ws in list(clients):
        try:
            await ws.send_text(text)
        except Exception:
            remove.append(ws)
    for ws in remove:
        clients.discard(ws)

async def binance_manager():
    reconnect_delay = 1.0
    ws_conn = None
    active_symbols_snapshot = set()

    while True:
        await _symbols_changed.wait()
        _symbols_changed.clear()
        await asyncio.sleep(0.6)

        async with _subscribe_lock:
            wanted = set(TOP_SYMBOLS) | set(dynamic_watches)
        if wanted == active_symbols_snapshot and ws_conn:
            continue

        if ws_conn:
            try:
                await ws_conn.close()
            except Exception:
                pass
            ws_conn = None
            active_symbols_snapshot = set()

        if not wanted:
            continue

        url = build_stream_url(list(wanted))
        try:
            print("Connecting to Binance for symbols:", sorted(wanted))
            ws_conn = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            print("Connected to Binance combined stream.")
            reconnect_delay = 1.0
            active_symbols_snapshot = set(wanted)

            while True:
                if _symbols_changed.is_set():
                    print("Symbol set changed, reconnecting to updated stream...")
                    break
                try:
                    msg = await asyncio.wait_for(ws_conn.recv(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed as e:
                    print("Binance WS closed:", e)
                    break
                except Exception as e:
                    print("Binance receive error:", e)
                    break

                try:
                    obj = json.loads(msg)
                    data = obj.get("data", {})
                    sym = data.get("s", "").lower()
                    price = data.get("p")
                    ts = data.get("E")
                    if not sym or price is None:
                        continue
                    prev = price_cache.get(sym, {}).get("price")
                    if prev != price:
                        price_cache[sym] = {"price": price, "ts": ts}
                        await broadcast_to_all(sym, {"price": price, "ts": ts})
                except Exception as e:
                    print("Error parsing Binance msg:", e)
                    continue

        except Exception as e:
            print("Error connecting to Binance:", e)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 30)
            continue

@app.on_event("startup")
async def startup_event():
    # populate valid symbols list first
    await fetch_valid_symbols()
    asyncio.create_task(binance_manager())
    # initial trigger for manager to connect to TOP_SYMBOLS
    async with _subscribe_lock:
        _symbols_changed.set()
    print("Startup complete.")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    print("Client connected:", websocket.client)
    try:
        snapshot = {s: price_cache.get(s) for s in TOP_SYMBOLS}
        await websocket.send_text(json.dumps({"type": "snapshot", "data": snapshot}))

        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
            except:
                await websocket.send_text(json.dumps({"error": "invalid json"}))
                continue
            action = msg.get("action")
            symbol = (msg.get("symbol") or "").lower().strip()
            if not symbol:
                await websocket.send_text(json.dumps({"error": "missing symbol"}))
                continue

            # Validate symbol against fetched exchangeInfo
            if symbol not in VALID_SYMBOLS:
                # immediately inform client symbol is invalid
                await websocket.send_text(json.dumps({"error": "invalid_symbol", "symbol": symbol}))
                continue

            if action == "watch":
                async with _subscribe_lock:
                    if symbol not in dynamic_watches and symbol not in TOP_SYMBOLS:
                        dynamic_watches.add(symbol)
                        _symbols_changed.set()
                if symbol in price_cache:
                    await websocket.send_text(json.dumps({"type": "price", "symbol": symbol, "data": price_cache[symbol]}))
            elif action == "unwatch":
                async with _subscribe_lock:
                    if symbol in dynamic_watches:
                        dynamic_watches.discard(symbol)
                        _symbols_changed.set()
                await websocket.send_text(json.dumps({"ok": True, "unwatched": symbol}))
            else:
                await websocket.send_text(json.dumps({"error": "unknown action"}))

    except WebSocketDisconnect:
        print("Client disconnected:", websocket.client)
    except Exception as e:
        print("WS error:", e)
    finally:
        clients.discard(websocket)

@app.get("/snapshot")
async def snapshot():
    return {"top": {s: price_cache.get(s) for s in TOP_SYMBOLS}, "dynamic": list(dynamic_watches)}

@app.get("/")
async def root():
    return FileResponse("static/index.html")
