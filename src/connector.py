import asyncio
import json
import logging
import threading
import time
import requests
import websockets
from typing import Callable, Dict, Any, List, Optional
from logger import LoggerSetup

logger = LoggerSetup.get_logger("IBConnector")

class IBConnector:
    def __init__(self, rest_url: str, ws_url: str):
        self.rest_url = rest_url
        self.ws_url = ws_url
        self.ws = None
        self.stop_event = threading.Event()
        self.callbacks: Dict[str, List[Callable]] = {} # topic -> list of callbacks
        self.thread = None

    def start(self):
        """Starts the WebSocket listener in a separate thread."""
        self.thread = threading.Thread(target=self._run_ws_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.ws:
            # We can't easily close from another thread in basic websockets without async
            # But the loop should check stop_event
            pass

    def subscribe(self, topic: str, callback: Callable):
        if topic not in self.callbacks:
            self.callbacks[topic] = []
        self.callbacks[topic].append(callback)
        # Send subscribe message if needed (RODSIC_GUI logic)
        # Assuming IB_Core accepts {action: "subscribe", topic: "..."}
        # But we do this in the loop after connection
    
    def _run_ws_loop(self):
        asyncio.run(self._listen_forever())

    async def _listen_forever(self):
        while not self.stop_event.is_set():
            try:
                logger.info(f"Connecting to {self.ws_url}...")
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    logger.info("Connected to IB_Core WebSocket.")
                    
                    # Subscribe to topics
                    # Based on RODSIC_GUI app.js logic
                    for topic in ["orders", "executions", "account"]:
                        await ws.send(json.dumps({"action": "subscribe", "topic": topic}))
                        logger.info(f"Subscribed to {topic}")

                    async for message in ws:
                        if self.stop_event.is_set():
                            break
                        self._handle_message(message)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                time.sleep(5) # Reconnect delay

    def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            topic = data.get("topic")
            # Handle 'update' and 'delta' types if needed, for strategies we care about data content
            payload = data.get("data")
            
            # Dispatch to callbacks
            if topic and topic in self.callbacks:
                for cb in self.callbacks[topic]:
                    try:
                        cb(payload, data.get("type"))
                    except Exception as e:
                        logger.error(f"Callback error for topic {topic}: {e}")
                        
        except json.JSONDecodeError:
            logger.error(f"Failed to decode WS message: {message}")

    # --- REST API Methods ---

    def place_bracket_order(self, bracket_data: Dict[str, Any]) -> dict:
        """
        Places a Bracket Order via REST API.
        Returns: A dictionary with the order IDs {"Parent": parent_id, "SL": sl_id, "TP": tp_id}
        """
        try:
            url = f"{self.rest_url}/Orders/PlaceBracket"
            response = requests.post(url, json=bracket_data)
            response.raise_for_status()
            result = response.json()
            return result.get("orderIds", {})
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to place bracket order: {e}")
            if e.response:
                logger.error(f"Response: {e.response.text}")
            return {}

    def get_open_orders(self) -> Optional[Dict[str, Any]]:
        """Fetch all open orders."""
        try:
            url = f"{self.rest_url}/Orders/ListAll"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return None

    def cancel_order(self, order_id: int) -> bool:
        try:
            url = f"{self.rest_url}/Orders/{order_id}"
            response = requests.delete(url)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def ensure_watchlist(self, symbols: List[str]):
        """
        Ensures that the given symbols are in the IB_Core Watchlist.
        We can use /Watchlist endpoint (POST) to add them.
        """
        # We need to add them one by one or batch if supported.
        # IB_Core API mostly supports one by one add via /Watchlist/Add?
        # Or maybe we can just use ContractAdhoc?
        # Actually, adding to Watchlist ensures we get market data.
        
        # Let's check available endpoints in our memory or just try standard POST /Watchlist
        # Assuming POST /Watchlist adds a symbol.
        url = f"{self.rest_url}/Watchlist"
        
        for symbol in symbols:
            try:
                # We need to be careful with the payload. 
                # Usually it expects a Contract object or just a symbol?
                # Let's try sending {symbol: ...}
                payload = {"symbol": symbol}
                # Run in thread executor because requests is blocking
                await asyncio.to_thread(requests.post, url, json=payload)
                logger.info(f"Added {symbol} to Watchlist")
            except Exception as e:
                logger.warning(f"Failed to add {symbol} to Watchlist: {e}")

    async def get_executions(self, strategy: str = None, symbol: str = None) -> List[Dict]:
        """
        Fetches execution history from IB_Core.
        """
        try:
            url = f"{self.rest_url}/Executions"
            params = {}
            if strategy: params['strategy'] = strategy
            if symbol: params['symbol'] = symbol
            
            # Use a longer timeout for historical query
            response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get executions: {e}")
            return []
