import asyncio
import logging
from typing import Dict, Any, List
import signal
import requests

from app_config import Config
from connector import IBConnector
from state_manager import StateManager
from strategies.pentagramaru import PentagramaRu
from logger import LoggerSetup

# Setup Logging
logger = LoggerSetup.get_logger("Service")

class StrategyApp:
    def __init__(self):
        # We assume strategies.yaml is in the parent directory of src
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        strat_path = os.path.join(base_dir, "strategies.yaml")
        
        self.config = Config(strategies_path=strat_path)
        self.state_manager = StateManager()
        
        ib_conf = self.config.ib_core
        self.connector = IBConnector(ib_conf.get('rest_url'), ib_conf.get('ws_url'))
        
        self.strategies = []
        self.msg_queue = asyncio.Queue()
        self.running = True
        self.background_task = None

    def _ws_callback(self, payload: Any, msg_type: str):
        if not self.running: return
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self.msg_queue.put_nowait, (msg_type, payload))
        except RuntimeError:
            pass

    def setup_strategies(self):
        strat_conf = self.config.strategies
        
        # Initialize PentagramaRu if enabled
        p_conf = strat_conf.get('pentagrama_ru')
        if p_conf and p_conf.get('enabled'):
            strat = PentagramaRu("pentagrama_ru", p_conf, self.connector, self.state_manager)
            self.strategies.append(strat)
            logger.info("Initialized PentagramaRu Strategy")

        # Initialize PentagramaES if enabled (from new config structure)
        p_es = strat_conf.get('pentagrama_es')
        if p_es and p_es.get('enabled'):
            strat = PentagramaRu("pentagrama_es", p_es, self.connector, self.state_manager)
            self.strategies.append(strat)
            logger.info("Initialized PentagramaES Strategy")

        # Initialize PentagramaButterfly if enabled (from new config structure)
        p_fly = strat_conf.get('pentagrama_butterfly')
        if p_fly and p_fly.get('enabled'):
            strat = PentagramaRu("pentagrama_butterfly", p_fly, self.connector, self.state_manager)
            self.strategies.append(strat)
            logger.info("Initialized PentagramaButterfly Strategy")

    async def run_loop(self):
        logger.info("Entering Main Event Loop...")
        while self.running:
            try:
                # Wait for message with timeout to allow shutdown check
                try:
                    msg_type, payload = await asyncio.wait_for(self.msg_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Dispatch
                for strat in self.strategies:
                    try:
                        if msg_type in ['update', 'delta', 'execution']:
                             # Check payload content to classify
                            if 'execId' in payload:
                                await strat.on_execution(payload)
                            elif 'orderId' in payload:
                                await strat.on_order_update(payload)
                                
                    except Exception as e:
                        logger.error(f"Error in strategy {strat.name} processing {msg_type}: {e}")
                
                self.msg_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main Loop Error: {e}")

    async def _boot_sequence(self):
        import os
        ib_port = int(os.getenv("IB_CORE_PORT", 8000))
        ib_host = os.getenv("IB_CORE_HOST", "localhost")
        ready_url = f"http://{ib_host}:{ib_port}/restAPI/System/Ready"
        
        logger.info(f"Waiting for IB_Core ({ready_url}) to be fully ready...")
        
        ib_ready = False
        while not ib_ready and self.running:
            try:
                # We use requests + asyncio.to_thread to avoid adding an aiohttp dependency
                response = await asyncio.to_thread(requests.get, ready_url, timeout=2)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("ready") is True:
                        ib_ready = True
                        logger.info("IB_Core is ready! Proceeding with RODSIC_Strat boot.")
                        break
            except Exception:
                pass
            logger.info("IB_Core not ready yet. Retrying in 3 seconds...")
            await asyncio.sleep(3)

        if not self.running: return

        try:
            await self._start_internal()
            self.is_ready = True
            logger.info("RODSIC_Strat boot sequence finished successfully.")
        except Exception as e:
            logger.error(f"Critical error during RODSIC_Strat boot sequence: {e}", exc_info=True)
            self.is_ready = False

    async def start(self):
        self.running = True
        self.is_ready = False
        self.boot_task = asyncio.create_task(self._boot_sequence())

    async def _start_internal(self):
        # 1. Start Connector
        self.connector.subscribe("orders", self._ws_callback)
        self.connector.subscribe("executions", self._ws_callback)
        self.connector.start()
        
        # 2. Setup Strategies
        self.setup_strategies()
        
        # 3. Watchlist Sync
        all_symbols = []
        for s in self.strategies:
            if hasattr(s, 'contracts'):
                for c in s.contracts:
                    sym = c.get('symbol')
                    if sym: all_symbols.append(sym)
            elif hasattr(s, 'contract_setup'):
                 # Fallback for other strategies if any
                symbol = s.contract_setup.get('symbol')
                if symbol: all_symbols.append(symbol)
        
        if all_symbols:
            logger.info(f"Ensuring Watchlist contains: {all_symbols}")
            try:
                await self.connector.ensure_watchlist(all_symbols)
            except Exception as e:
                logger.error(f"Failed to sync watchlist: {e}")

        # 4. Start Strategies
        for strat in self.strategies:
            try:
                await strat.start()
            except Exception as e:
                logger.error(f"Failed to start strategy {strat.name}: {e}")

        # 5. Start Loop
        self.background_task = asyncio.create_task(self.run_loop())

    async def stop(self):
        self.running = False
        logger.info("Shutting down...")
        self.connector.stop()
        for strat in self.strategies:
            strat.stop()
        
        if self.background_task:
            self.background_task.cancel()
            try:
                await self.background_task
            except asyncio.CancelledError:
                pass

    async def reload(self):
        logger.info("Reloading strategies from configuration...")
        
        # 1. Stop all current strategies
        for strat in self.strategies:
            strat.stop()
        self.strategies.clear()
        
        # 2. Reload Configuration
        try:
            self.config.strategies_data = self.config._load_strategies()
        except Exception as e:
            logger.error(f"Failed to reload strategy config: {e}")
            
        # 3. Setup again
        self.setup_strategies()
        
        # 4. Sync Watchlist and Start
        all_symbols = []
        for s in self.strategies:
            if hasattr(s, 'contracts'):
                for c in s.contracts:
                    sym = c.get('symbol')
                    if sym: all_symbols.append(sym)
            elif hasattr(s, 'contract_setup'):
                symbol = s.contract_setup.get('symbol')
                if symbol: all_symbols.append(symbol)
                
        if all_symbols:
            logger.info(f"Ensuring Watchlist contains: {all_symbols}")
            try:
                await self.connector.ensure_watchlist(all_symbols)
            except Exception as e:
                logger.error(f"Failed to sync watchlist during reload: {e}")
                
        for strat in self.strategies:
            try:
                await strat.start()
            except Exception as e:
                logger.error(f"Failed to start strategy {strat.name} during reload: {e}")
                
        logger.info("Strategies reloaded successfully.")

# Global Instance
strategy_service = StrategyApp()
