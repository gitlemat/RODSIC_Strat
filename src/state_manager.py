import json
import os
import logging
from typing import Dict, Any, Optional
from logger import LoggerSetup

logger = LoggerSetup.get_logger("StateManager")

class StateManager:
    def __init__(self, state_file: str = "strat_state.json"):
        # Put it in the same directory as strategies.yaml (RODSIC_Strat root)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.state_file = os.path.join(base_dir, state_file)
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state file: {e}")
            return {}

    def save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save state file: {e}")

    def get_strategy_state(self, strategy_name: str) -> Dict[str, Any]:
        return self.state.get(strategy_name, {})

    def update_strategy_state(self, strategy_name: str, key: str, value: Any):
        if strategy_name not in self.state:
            self.state[strategy_name] = {}
        
        self.state[strategy_name][key] = value
        self.save_state()

    def clear_strategy_state(self, strategy_name: str):
        if strategy_name in self.state:
            del self.state[strategy_name]
            self.save_state()
