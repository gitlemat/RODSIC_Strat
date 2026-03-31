import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
from .base import BaseStrategy
from connector import IBConnector
from state_manager import StateManager
from position_tracker import PositionTracker
from logger import LoggerSetup
from utils import parse_symbol_expiry
from logger import LoggerSetup

logger = LoggerSetup.get_logger("PentagramaRu")

class PentagramaRu(BaseStrategy):
    def __init__(self, name: str, config: Dict[str, Any], connector, state_manager):
        super().__init__(name, config, connector, state_manager)
        
        # Load all ENABLED contracts
        self.contracts = []
        for c in self.config.get('contracts', []):
            if c.get('enabled', True):
                self.contracts.append(c)
        
        # State: composite_id (symbol::level_id) -> {parentId, tpId, slId, status, config}
        self.runtime_state = {} 
        # Reverse map: order_id -> {composite_id, type}
        self.order_map = {}
        
        # Position Trackers per Symbol
        self.trackers: Dict[str, PositionTracker] = {}

    async def start(self):
        logger.info(f"Starting Strategy {self.name} with {len(self.contracts)} active contracts")
        
        if not self.contracts:
            logger.warning("No enabled contracts found for this strategy.")
            logger.warning("No enabled contracts found for this strategy.")
            return

        # 0. Initialize PnL from history
        await self._initialize_pnl()

        # 1. Load persisted state
        saved_state = self.state_manager.get_strategy_state(self.name)
        if saved_state:
            self.runtime_state = saved_state.get('levels', {})
            # Rebuild order map
            for cid, data in self.runtime_state.items():
                if 'parentId' in data: self.order_map[data['parentId']] = {'composite_id': cid, 'type': 'PARENT'}
                if 'tpId' in data: self.order_map[data['tpId']] = {'composite_id': cid, 'type': 'TP'}
                if 'slId' in data: self.order_map[data['slId']] = {'composite_id': cid, 'type': 'SL'}
        
        # 2. Get Open Orders from IB
        open_orders_full = await asyncio.to_thread(self.connector.get_open_orders)
        if open_orders_full is None:
            logger.error("Failed to fetch open orders from IB_Core. Aborting strategy start to prevent state corruption.")
            return
            
        # Extract active orders and drop inactive
        active_ib_orders = self._parse_active_orders(open_orders_full)
        self._safe_to_persist = True

        # 3. Reconcile Levels for EACH contract
        for contract in self.contracts:
            symbol = contract.get('symbol')
            for level in contract.get('levels', []):
                await self._reconcile_single_level(symbol, level, contract, active_ib_orders, open_orders_full)

        self._persist()

    def _parse_active_orders(self, open_orders_full: Any) -> set:
        active_ib_orders = set()
        inactive_statuses = ['Cancelled', 'Inactive', 'ApiCancelled', 'PendingCancel']

        if isinstance(open_orders_full, dict):
            for oid_str, order_info in open_orders_full.items():
                if isinstance(order_info, dict) and order_info.get('status') not in inactive_statuses:
                    active_ib_orders.add(str(oid_str))
        elif isinstance(open_orders_full, list):
             for o in open_orders_full:
                 if isinstance(o, dict) and o.get('status') not in inactive_statuses:
                     active_ib_orders.add(str(o.get('orderId')))
        return active_ib_orders

    def _get_parent_status(self, pid: str, open_orders_full: Any) -> Optional[str]:
        if isinstance(open_orders_full, dict):
            parent_info = open_orders_full.get(int(pid)) or open_orders_full.get(str(pid))
            if isinstance(parent_info, dict):
                return parent_info.get('status')
        elif isinstance(open_orders_full, list):
            for o in open_orders_full:
                if str(o.get('orderId')) == str(pid):
                    return o.get('status')
        return None

    async def _reconcile_single_level(self, symbol: str, level: dict, contract: dict, active_ib_orders: set, open_orders_full: Any):
        lid = str(level['id'])
        cid = f"{symbol}::{lid}" # Composite ID
        
        if cid not in self.runtime_state:
            # New Level
            if contract.get('auto_recreate', True):
                logger.info(f"[{symbol}] Level {lid}: New. Placing orders.")
                await self._place_level(contract, level, cid)
            else:
                logger.info(f"[{symbol}] Level {lid}: New, but auto_recreate is False. Skipping initial placement.")
            return

        state = self.runtime_state[cid]
        pid = str(state.get('parentId')) if state.get('parentId') else None
        tp_alive = str(state.get('tpId')) in active_ib_orders
        sl_alive = str(state.get('slId')) in active_ib_orders
        
        if pid and pid not in active_ib_orders:
            parent_status = self._get_parent_status(pid, open_orders_full)
            inactive_statuses = ['Cancelled', 'Inactive', 'ApiCancelled', 'PendingCancel']
            
            if parent_status in inactive_statuses:
                if contract.get('auto_fix', False):
                    logger.warning(f"[{symbol}] Level {lid}: Parent cancelled. AutoFixing (Resetting).")
                    await self._reset_level(symbol, lid, cid)
                else:
                    logger.info(f"[{symbol}] Level {lid}: Parent cancelled, but auto_fix is False. Halting.")
                    state['status'] = 'ERROR_PARENT_CANCELLED'
            else:
                # Parent missing and not "Cancelled" -> Assume it filled and left open_orders
                if tp_alive or sl_alive:
                    logger.info(f"[{symbol}] Level {lid}: Parent missing, children active. Status: MONITOR_EXIT")
                    state['status'] = 'MONITOR_EXIT'
                else:
                    if contract.get('auto_fix', False):
                        logger.info(f"[{symbol}] Level {lid}: All orders missing. AutoFixing (Regenerating).")
                        await self._reset_level(symbol, lid, cid)
                    else:
                        logger.info(f"[{symbol}] Level {lid}: All orders missing, but auto_fix is False. Halting.")
                        state['status'] = 'ERROR_MISSING_ORDERS'
        else:
            # Parent exists, check children
            if not tp_alive or not sl_alive:
                logger.warning(f"[{symbol}] Level {lid}: Parent active but TP/SL missing. Recreating bracket.")
                if contract.get('auto_fix', False):
                    await self._cancel_and_reset_level(symbol, lid, cid, state)
                else:
                    state['status'] = 'ERROR_CHILDREN_MISSING'
            else:
                logger.info(f"[{symbol}] Level {lid}: Parent and children active. Status: MONITOR_ENTRY")
                state['status'] = 'MONITOR_ENTRY'

    async def _cancel_and_reset_level(self, symbol: str, lid: str, cid: str, state: dict):
        """Cancels remaining active orders for a level and resets it."""
        logger.info(f"[{symbol}] Manual Fix: Starting cancellation for Level {lid}")
        for key in ['parentId', 'tpId', 'slId']:
            oid = state.get(key)
            if oid:
                try:
                    await asyncio.to_thread(self.connector.cancel_order, oid)
                    logger.info(f"[{symbol}] Cancelled hanging order {oid} for Level {lid}")
                except Exception as e:
                    logger.error(f"[{symbol}] Failed to cancel {oid}: {e}")
        
        await asyncio.sleep(1) # Yield time for cancellations to process
        logger.info(f"[{symbol}] Manual Fix: Cancellations finished, proceeding to reset Level {lid}")
        await self._reset_level(symbol, lid, cid)

    def _persist(self):
        """Saves current runtime state to disk"""
        if not getattr(self, '_safe_to_persist', False):
            logger.warning(f"[{self.name}] Skipped persisting state to disk because the strategy is not in a validated state.")
            return
        self.state_manager.update_strategy_state(self.name, "levels", self.runtime_state)

    def stop(self):
        pass

    async def on_order_update(self, order_data: Dict[str, Any]):
        order_id = order_data.get('orderId')
        status = order_data.get('status')
        filled = float(order_data.get('filled', 0))
        remaining = float(order_data.get('remaining', 0))
        
        if order_id not in self.order_map:
            return

        info = self.order_map[order_id]
        cid = info['composite_id'] # symbol::level_id
        order_type = info['type']
        
        # Parse CID to get symbol and level if needed
        symbol, lid = cid.split('::')
        
        logger.info(f"[{symbol}] Update Level {lid} ({order_type}): {status} Filled:{filled}")
        
        current_state = self.runtime_state.get(cid)
        if not current_state: return
        
        contract_cfg = next((c for c in self.contracts if c['symbol'] == symbol), None)
        if not contract_cfg: return

        if order_type == 'PARENT':
            await self._handle_parent_update(symbol, lid, cid, current_state, contract_cfg, order_id, status, filled, remaining)
        elif order_type in ['TP', 'SL']:
            await self._handle_child_update(symbol, lid, cid, current_state, contract_cfg, order_type, order_id, status, filled, remaining)

    async def _handle_parent_update(self, symbol: str, lid: str, cid: str, current_state: dict, contract_cfg: dict, order_id: Any, status: str, filled: float, remaining: float):
        # Ignore broker feedback (intentional cancellations) while the fix is in progress
        if current_state.get('status') == 'FIXING':
            return
            
        if str(current_state.get('parentId')) != str(order_id):
            logger.debug(f"[{symbol}] Ignoring update for old PARENT {order_id} (active is {current_state.get('parentId')})")
            return
            
        if status == 'Filled' or (remaining == 0 and filled > 0):
            if current_state.get('status') != 'MONITOR_EXIT':
                logger.info(f"[{symbol}] Level {lid}: Parent Filled. Switching to MONITOR_EXIT")
                current_state['status'] = 'MONITOR_EXIT'
                self._persist()
        elif status in ['Cancelled', 'Inactive', 'ApiCancelled', 'PendingCancel']:
             if filled == 0:
                 if contract_cfg.get('auto_fix', False):
                     logger.warning(f"[{symbol}] Level {lid}: Parent cancelled. AutoFixing (Resetting).")
                     await self._reset_level(symbol, lid, cid)
                 else:
                     logger.info(f"[{symbol}] Level {lid}: Parent cancelled, but auto_fix is False. Halting.")
                     current_state['status'] = 'ERROR_PARENT_CANCELLED'
                     self._persist()

    async def _handle_child_update(self, symbol: str, lid: str, cid: str, current_state: dict, contract_cfg: dict, order_type: str, order_id: Any, status: str, filled: float, remaining: float):
        # Ignore broker feedback (intentional cancellations) while the fix is in progress
        if current_state.get('status') == 'FIXING':
            return
            
        active_id = current_state.get('tpId') if order_type == 'TP' else current_state.get('slId')
        if str(active_id) != str(order_id):
            logger.debug(f"[{symbol}] Ignoring update for old {order_type} {order_id} (active is {active_id})")
            return
            
        if status == 'Filled' or (remaining == 0 and filled > 0):
            logger.info(f"[{symbol}] Level {lid}: {order_type} Filled. Regenerating...")
            await asyncio.sleep(1) 
            
            if contract_cfg.get('auto_recreate', True):
                level_cfg = next((l for l in contract_cfg['levels'] if str(l['id']) == lid), None)
                if level_cfg:
                    await self._place_level(contract_cfg, level_cfg, cid)
                    self._persist()
            else:
                logger.info(f"[{symbol}] Level {lid}: {order_type} Filled, but auto_recreate is False. Halting loop.")
                current_state['status'] = 'FINISHED'
                self._persist()
                
        elif status in ['Cancelled', 'Inactive', 'ApiCancelled', 'PendingCancel']:
            if current_state.get('status') == 'MONITOR_ENTRY':
                if contract_cfg.get('auto_fix', False):
                    logger.warning(f"[{symbol}] Level {lid}: {order_type} Cancelled while in MONITOR_ENTRY. AutoFixing (Recreating bracket).")
                    await self._cancel_and_reset_level(symbol, lid, cid, current_state)
                else:
                    logger.info(f"[{symbol}] Level {lid}: {order_type} Cancelled, but auto_fix is False. Halting.")
                    current_state['status'] = 'ERROR_CHILD_CANCELLED'
                    self._persist()
            elif current_state.get('status') == 'MONITOR_EXIT':
                if contract_cfg.get('auto_fix', False):
                    logger.warning(f"[{symbol}] Level {lid}: {order_type} Cancelled while in MONITOR_EXIT. AutoFixing (Recreating bracket).")
                    logger.info(f"[{symbol}] Level {lid}: Cannot safely AutoFix a broken child during MONITOR_EXIT without risking double entry. Halting.")
                    current_state['status'] = 'ERROR_CHILD_CANCELLED_IN_MARKET'
                    self._persist()
                else:
                    logger.info(f"[{symbol}] Level {lid}: {order_type} Cancelled in MONITOR_EXIT, but auto_fix is False. Halting.")
                    current_state['status'] = 'ERROR_CHILD_CANCELLED_IN_MARKET'
                    self._persist()
    async def on_execution(self, exec_data: Dict[str, Any]):
        """
        Handles execution reports.
        """
        symbol = exec_data.get('symbol')
        order_id = exec_data.get('orderId')
        
        # Update Tracker
        if symbol:
            logger.info(f"[{symbol}] EXECUTION: {exec_data.get('side')} {exec_data.get('quantity')} @ {exec_data.get('fillPrice')} (Order: {order_id})")
            if symbol not in self.trackers:
                c_info = await self.connector.get_contract_info(symbol)
                multiplier = c_info.get('multiplier', 1.0) if c_info else 1.0
                self.trackers[symbol] = PositionTracker(symbol, multiplier=multiplier)
            
            self.trackers[symbol].add_execution(
                side=exec_data.get('side'),
                qty=float(exec_data.get('quantity', 0)),
                price=float(exec_data.get('fillPrice', 0))
            )

    async def _initialize_pnl(self):
        """
        Fetches historical executions for this strategy and rebuilds PositionTracker state.
        """
        logger.info(f"[{self.name}] Initializing PnL from history...")
        executions = await self.connector.get_executions(strategy=self.name)
        
        for exec_data in executions:
            symbol = exec_data.get('symbol')
            if not symbol: continue
            
            if symbol not in self.trackers:
                c_info = await self.connector.get_contract_info(symbol)
                multiplier = c_info.get('multiplier', 1.0) if c_info else 1.0
                self.trackers[symbol] = PositionTracker(symbol, multiplier=multiplier)
                
            qty = float(exec_data.get('qty', 0))
            price = float(exec_data.get('price', 0))
            side = exec_data.get('side', 'BOT')
            
            self.trackers[symbol].add_execution(side, qty, price)
            
        # Log results
        for sym, tracker in self.trackers.items():
            state = tracker.get_state()
            logger.info(f"[{self.name}] {sym} State restored: Pos={state['netPosition']}, Avg={state['avgCost']:.2f}, PnL={state['realizedPnL']:.2f}")

    async def _place_level(self, contract: Dict[str, Any], level: Dict[str, Any], cid: str):
        symbol = contract.get('symbol')
        lid = str(level['id'])
        
        # Resolve Expiry
        expiry = contract.get('lastTradeDateOrContractMonth')
        if not expiry:
            expiry = parse_symbol_expiry(symbol)
            if not expiry:
                logger.warning(f"[{symbol}] Could not infer expiry.")
        
        bracket_payload = {
            "symbol": symbol,
            "secType": contract.get('secType', 'FUT'),
            "exchange": contract.get('exchange', 'CME'),
            "currency": contract.get('currency', 'USD'),
            "lastTradeDateOrContractMonth": expiry or "",
            
            "action": level['action'],
            "qty": level['qty'],
            "LmtPrice": level['price'],
            "LmtPriceTP": level['tp_price'],
            "LmtPriceSL": level['sl_price'],
            "tif": "GTC",
            "orderRef": self.name # Tag with Strategy Name
        }
        
        logger.info(f"[{symbol}] Attempting to place new bracket for Level {lid}...")
        try:
            order_ids = await asyncio.to_thread(self.connector.place_bracket_order, bracket_payload)
            
            parent_id = order_ids.get("Parent")
            sl_id = order_ids.get("SL")
            tp_id = order_ids.get("TP")
            
            if parent_id and sl_id and tp_id:
                logger.info(f"[{symbol}] Placed Level {lid}. ParentId: {parent_id}, SL: {sl_id}, TP: {tp_id}")
                
                # Map logical roles to actual broker Order IDs
                self.runtime_state[cid] = {
                    'parentId': parent_id,
                    'tpId': tp_id,
                    'slId': sl_id,
                    'status': 'MONITOR_ENTRY',
                    'config': level
                }
                
                self.order_map[parent_id] = {'composite_id': cid, 'type': 'PARENT'}
                self.order_map[sl_id] = {'composite_id': cid, 'type': 'SL'}
                self.order_map[tp_id] = {'composite_id': cid, 'type': 'TP'}
                return True
            else:
                logger.error(f"[{symbol}] Failed to place bracket Level {lid}. Received truncated IDs: {order_ids}")
                return False
        except Exception as e:
            logger.error(f"[{symbol}] Exception during bracket placement for Level {lid}: {e}")
            return False

    async def _reset_level(self, symbol: str, lid: str, cid: str):
        """Finds configuration for a level and re-places orders."""
        # Normalize symbol for robust matching (Stripping and Case-Insensitivity)
        search_symbol = symbol.strip().upper()
        
        # 1. FIND CONFIG FIRST: Match symbol case-insensitively
        contract_cfg = next((c for c in self.contracts if c.get('symbol', '').strip().upper() == search_symbol), None)
        
        if not contract_cfg:
            logger.error(f"[{symbol}] Manual Fix Failed: Contract not found in ACTIVE contracts list. Check if strategy row is toggled 'Enabled'.")
            return
            
        level_cfg = next((l for l in contract_cfg.get('levels', []) if str(l.get('id')) == lid), None)
        if not level_cfg:
            logger.error(f"[{symbol}] Manual Fix Failed: Config for Level {lid} not found in YAML.")
            return

        # ATTEMPT RE-PLACEMENT FIRST
        # If successful, _place_level will overwrite the entry in self.runtime_state[cid]
        logger.info(f"[{symbol}] Attempting re-placement for Level {lid}...")
        success = await self._place_level(contract_cfg, level_cfg, cid)
        
        if success:
            logger.info(f"[{symbol}] Level {lid} successfully reset and re-placed.")
            self._persist()
        else:
            logger.error(f"[{symbol}] Level {lid} reset FAILED to place new bracket orders. Current state preserved.")

    async def on_auto_recreate_changed(self, symbol: str, auto_recreate: bool):
        logger.info(f"[{self.name}] auto_recreate changed for {symbol} to {auto_recreate}")
        if not auto_recreate:
            logger.info(f"[{symbol}] Cancelling all unexecuted parent orders to stop new entries...")
            for cid, state in list(self.runtime_state.items()):
                if cid.startswith(f"{symbol}::"):
                    if state.get('status') == 'MONITOR_ENTRY':
                        parent_id = state.get('parentId')
                        if parent_id:
                            lid = cid.split('::')[1]
                            logger.info(f"[{symbol}] Cancelling pending parent ID {parent_id} for Level {lid}")
                            try:
                                # Run in thread as cancel_order uses requests synchronously
                                await asyncio.to_thread(self.connector.cancel_order, parent_id)
                            except Exception as e:
                                logger.error(f"[{symbol}] Failed to cancel parent {parent_id}: {e}")

    async def on_contract_enabled_changed(self, symbol: str, enabled: bool):
        logger.info(f"[{self.name}] {symbol} enabled set to {enabled}")
        
        # Find raw config from self.config (which contains all, not just enabled)
        contract_cfg = next((c for c in self.config.get('contracts', []) if c['symbol'] == symbol), None)
        if not contract_cfg: return

        if enabled:
            # If not in self.contracts, add it
            if not any(c['symbol'] == symbol for c in self.contracts):
                self.contracts.append(contract_cfg)
            
            logger.info(f"[{symbol}] Contract enabled. Initializing levels...")
            levels = contract_cfg.get('levels', [])
            for level in levels:
                lid = str(level['id'])
                cid = f"{symbol}::{lid}"
                # If it's not active, place orders
                if cid not in self.runtime_state or self.runtime_state[cid].get('status') in ['DISABLED', 'FINISHED']:
                    if contract_cfg.get('auto_recreate', True):
                        await self._place_level(contract_cfg, level, cid)
            self._persist()
        else:
            logger.info(f"[{symbol}] Contract disabled. Cancelling all orders...")
            for cid, state in list(self.runtime_state.items()):
                if cid.startswith(f"{symbol}::") and state.get('status') != 'DISABLED':
                    state['status'] = 'DISABLED'
                    # Cancel any active orders (PARENT, TP, SL)
                    for key in ['parentId', 'tpId', 'slId']:
                        oid = state.get(key)
                        if oid:
                            try:
                                await asyncio.to_thread(self.connector.cancel_order, oid)
                            except Exception as e:
                                logger.error(f"[{symbol}] Failed to cancel {key} {oid}: {e}")
            self._persist()

    async def on_auto_fix_changed(self, symbol: str, auto_fix: bool):
        logger.info(f"[{self.name}] auto_fix changed for {symbol} to {auto_fix}")
        # Could automatically trigger fixes here if it transitioned to True, but for simplicity, wait for next event or manual trigger.

    async def manual_fix_level(self, symbol: str, lid: str):
        """
        Triggered manually by the API to recover an errored level (or even manually force a reset).
        """
        cid = f"{symbol}::{lid}"
        if cid not in self.runtime_state:
            raise ValueError(f"Level {lid} for {symbol} not found in active runtime_state.")
            
        logger.info(f"[{self.name}] Manual fix requested for {symbol} Level {lid}. Setting status to FIXING.")
        
        current_state = self.runtime_state[cid]
        # Set transitional status for UX (Survives page reload)
        current_state['status'] = "FIXING"
        
        # It's safest to cancel any dangling children and reset
        await self._cancel_and_reset_level(symbol, lid, cid, current_state)

    async def assume_order_executed(self, symbol: str, lid: str, order_type: str):
        """
        Manually injects a synthetic execution for a specific order and advances the state machine.
        order_type can be 'PARENT', 'TP', or 'SL'.
        """
        cid = f"{symbol}::{lid}"
        if cid not in self.runtime_state:
            raise ValueError(f"Level {lid} not found in active runtime_state.")
            
        current_state = self.runtime_state[cid]
        
        # Determine order side and original quantities based on strategy configuration
        contract_cfg = next((c for c in self.contracts if c['symbol'] == symbol), None)
        if not contract_cfg:
            raise ValueError(f"Contract config for {symbol} not found.")
            
        level_cfg = next((l for l in contract_cfg['levels'] if str(l['id']) == lid), None)
        if not level_cfg:
            raise ValueError(f"Level {lid} config not found.")
            
        qty = float(level_cfg['qty'])
        action = level_cfg['action'].upper() # Action of the Parent
        
        if order_type == 'PARENT':
            if current_state.get('status') == 'MONITOR_EXIT':
                raise ValueError("Cannot assume Parent Executed: Level is already in MONITOR_EXIT (Parent was already executed).")
            
            # Inject synthetic fill for Parent
            price = float(level_cfg['price'])
            if symbol not in self.trackers:
                c_info = await self.connector.get_contract_info(symbol)
                multiplier = c_info.get('multiplier', 1.0) if c_info else 1.0
                self.trackers[symbol] = PositionTracker(symbol, multiplier=multiplier)
                
            self.trackers[symbol].add_execution(side=action, qty=qty, price=price)
            logger.info(f"[{symbol}] Level {lid}: Manual ASSUME EXECUTED for PARENT. Injected synthetic fill ({action} {qty} @ {price}). Status -> MONITOR_EXIT.")
            
            current_state['status'] = 'MONITOR_EXIT'
            self._persist()
            
        elif order_type in ['TP', 'SL']:
            if current_state.get('status') != 'MONITOR_EXIT':
                raise ValueError(f"Cannot assume {order_type} Executed: Level must be in MONITOR_EXIT first (Parent must be executed first).")
                
            # Inject synthetic fill for Child (opposite action of Parent)
            child_action = 'SELL' if action == 'BUY' else 'BUY'
            price = float(level_cfg['tp_price']) if order_type == 'TP' else float(level_cfg['sl_price'])
            
            if symbol not in self.trackers:
                c_info = await self.connector.get_contract_info(symbol)
                multiplier = c_info.get('multiplier', 1.0) if c_info else 1.0
                self.trackers[symbol] = PositionTracker(symbol, multiplier=multiplier)
                
            self.trackers[symbol].add_execution(side=child_action, qty=qty, price=price)
            logger.info(f"[{symbol}] Level {lid}: Manual ASSUME EXECUTED for {order_type}. Injected synthetic fill ({child_action} {qty} @ {price}).")
            
            # The level cycle is complete. React like a normal fill.
            if contract_cfg.get('auto_recreate', True):
                logger.info(f"[{symbol}] Level {lid}: Regenerating level after assumed child fill...")
                await self._place_level(contract_cfg, level_cfg, cid)
                self._persist()
            else:
                logger.info(f"[{symbol}] Level {lid}: Cycle complete via Assume Executed, but auto_recreate is False. Halting.")
                current_state['status'] = 'FINISHED'
                self._persist()
        else:
            raise ValueError("order_type must be 'PARENT', 'TP', or 'SL'.")
