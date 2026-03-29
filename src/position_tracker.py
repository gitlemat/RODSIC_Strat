from collections import deque
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger("PositionTracker")

class PositionTracker:
    def __init__(self, symbol: str, multiplier: float = 1.0):
        self.symbol = symbol
        self.multiplier = multiplier
        self.net_position = 0.0
        self.avg_cost = 0.0
        self.realized_pnl = 0.0
        self.total_commission = 0.0
        
        # FIFO Queue for open lots: [{'qty': float, 'price': float}, ...]
        self.open_lots = deque()

    def add_execution(self, side: str, qty: float, price: float, commission: float = 0.0):
        """
        Updates position state with a new execution using FIFO logic.
        side: 'BOT' (Buy) or 'SLD' (Sell)
        qty: Absolute quantity (always positive)
        price: Fill price
        """
        self.total_commission += commission
        
        # Normalize direction
        # BOT -> +1, SLD -> -1
        direction = 1 if side in ['BOT', 'BUY'] else -1
        signed_qty = qty * direction
        
        # Case 1: Increasing Position (or opening new one)
        # If current pos is 0, or same sign as trade
        if self.net_position == 0 or (self.net_position > 0 and direction > 0) or (self.net_position < 0 and direction < 0):
            self._add_lot(qty, price)
            self._update_avg_cost(qty, price)
            self.net_position += signed_qty
            
        # Case 2: Decrease/Close Position (Partial or Full)
        else:
            remaining_trade_qty = qty
            
            while remaining_trade_qty > 0 and self.open_lots:
                match_lot = self.open_lots[0]
                match_qty = match_lot['qty']
                match_price = match_lot['price']
                
                # How much we can match in this iteration
                filled_qty = min(remaining_trade_qty, match_qty)
                
                # Calculate PnL for this chunk
                # If we were Long (net > 0), we represent Sell Price - Buy Price
                # If we were Short (net < 0), we represent Sell Price - Buy Price (logic holds if we track signs correctly)
                
                # Easier:
                # Value Sold = filled_qty * price
                # Cost Basis = filled_qty * match_price
                # If Closing Long: PnL = (price - match_price) * filled_qty
                # If Closing Short: PnL = (match_price - price) * filled_qty
                
                if self.net_position > 0: # Closing Long
                    pnl = (price - match_price) * filled_qty * self.multiplier
                else: # Closing Short
                    pnl = (match_price - price) * filled_qty * self.multiplier
                    
                self.realized_pnl += pnl
                
                # Update Lot
                if filled_qty >= match_qty:
                    self.open_lots.popleft() # Fully consumed this lot
                else:
                    match_lot['qty'] -= filled_qty # Partially consumed
                    
                remaining_trade_qty -= filled_qty
                
                # Update Net Position (approaching zero)
                self.net_position += (filled_qty * direction)

            # Case 3: Reversal (Flip)
            # If we simply closed out, remaining_trade_qty is 0.
            # If we verified a flip, remaining_trade_qty > 0.
            if remaining_trade_qty > 0:
                # We have flipped to the other side with the remainder
                self._add_lot(remaining_trade_qty, price)
                
                # Reset avg cost for the new position side
                self.avg_cost = price 
                
                self.net_position += (remaining_trade_qty * direction)

        # Update Avg Cost Display (Weighted Average of Open Lots)
        self._recalc_avg_cost()
        
        logger.debug(f"[{self.symbol}] Tracker Update: Pos={self.net_position}, Avg={self.avg_cost:.2f}, RealPnL={self.realized_pnl:.2f}")

    def _add_lot(self, qty: float, price: float):
        self.open_lots.append({'qty': qty, 'price': price})

    def _update_avg_cost(self, new_qty: float, new_price: float):
        """Standard Weighted Average Update for increasing position."""
        current_abs_qty = abs(self.net_position)
        total_qty = current_abs_qty + new_qty
        if total_qty > 0:
            self.avg_cost = ((current_abs_qty * self.avg_cost) + (new_qty * new_price)) / total_qty

    def _recalc_avg_cost(self):
        """Recalculates from open lots to be precise."""
        if not self.open_lots:
            self.avg_cost = 0.0
            return
            
        total_val = sum(l['qty'] * l['price'] for l in self.open_lots)
        total_qty = sum(l['qty'] for l in self.open_lots)
        
        if total_qty > 0:
            self.avg_cost = total_val / total_qty
        else:
            self.avg_cost = 0.0

    def get_state(self) -> Dict[str, Any]:
        return {
            "netPosition": self.net_position,
            "avgCost": self.avg_cost,
            "realizedPnL": self.realized_pnl,
            "totalCommission": self.total_commission
        }
