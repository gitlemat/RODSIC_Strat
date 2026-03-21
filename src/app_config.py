import yaml
import os
from typing import Dict, Any
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Config:
    def __init__(self, strategies_path: str = "strategies.yaml"):
        self.strategies_path = strategies_path
        self.strategies_data = self._load_strategies()

    def _load_strategies(self) -> Dict[str, Any]:
        if not os.path.exists(self.strategies_path):
            raise FileNotFoundError(f"Strategies file not found: {self.strategies_path}")
        
        with open(self.strategies_path, 'r') as f:
            return yaml.safe_load(f)

    @property
    def ib_core(self) -> Dict[str, Any]:
        base_url = os.getenv("IB_CORE_URL", "http://localhost:8000")
        return {
            'rest_url': f"{base_url}/restAPI",
            'ws_url': f"{base_url.replace('http', 'ws')}/restAPI/ws",
            'reconnect_interval': int(os.getenv("RECONNECT_INTERVAL", 5))
        }

    @property
    def strategies(self) -> Dict[str, Any]:
        return self.strategies_data.get('strategies', {})

    def get_strategy_config(self, strategy_name: str) -> Dict[str, Any]:
        return self.strategies.get(strategy_name, {})

    def toggle_strategy_contract(self, strategy_name: str, symbol: str, enabled: bool) -> bool:
        """
        Updates the enabled status of a specific contract in a strategy,
        persisting it to strategies.yaml while preserving comments.
        Returns True if updated successfully, False if contract/strategy not found.
        """
        try:
            import ruamel.yaml
        except ImportError:
            raise ImportError("ruamel.yaml is required for modifying config while preserving comments")

        yaml_parser = ruamel.yaml.YAML()
        yaml_parser.preserve_quotes = True
        
        try:
            with open(self.strategies_path, 'r') as f:
                data = yaml_parser.load(f)
            
            strat_node = data.get('strategies', {}).get(strategy_name)
            if not strat_node:
                return False
                
            contracts = strat_node.get('contracts', [])
            found = False
            for contract in contracts:
                if contract.get('symbol') == symbol:
                    contract['enabled'] = enabled
                    found = True
                    break
                    
            if not found:
                return False
                
            # Save back to file
            with open(self.strategies_path, 'w') as f:
                yaml_parser.dump(data, f)
            
            # Immediately update in-memory read-only copy as well
            if strategy_name in self.strategies_data['strategies']:
                mem_strat = self.strategies_data['strategies'][strategy_name]
                for mem_c in mem_strat.get('contracts', []):
                    if mem_c.get('symbol') == symbol:
                        mem_c['enabled'] = enabled
            
            return True
            
        except Exception as e:
            print(f"Failed to toggle strategy config: {e}")
            return False

    def toggle_strategy_auto_recreate(self, strategy_name: str, symbol: str, auto_recreate: bool) -> bool:
        """
        Updates the auto_recreate status of a specific contract in a strategy,
        persisting it to strategies.yaml while preserving comments.
        Returns True if updated successfully, False if contract/strategy not found.
        """
        try:
            import ruamel.yaml
        except ImportError:
            raise ImportError("ruamel.yaml is required for modifying config while preserving comments")

        yaml_parser = ruamel.yaml.YAML()
        yaml_parser.preserve_quotes = True
        
        try:
            with open(self.strategies_path, 'r') as f:
                data = yaml_parser.load(f)
            
            strat_node = data.get('strategies', {}).get(strategy_name)
            if not strat_node:
                return False
                
            contracts = strat_node.get('contracts', [])
            found = False
            for contract in contracts:
                if contract.get('symbol') == symbol:
                    contract['auto_recreate'] = auto_recreate
                    found = True
                    break
                    
            if not found:
                return False
                
            with open(self.strategies_path, 'w') as f:
                yaml_parser.dump(data, f)
            
            if strategy_name in self.strategies_data['strategies']:
                mem_strat = self.strategies_data['strategies'][strategy_name]
                for mem_c in mem_strat.get('contracts', []):
                    if mem_c.get('symbol') == symbol:
                        mem_c['auto_recreate'] = auto_recreate
            
            return True
            
        except Exception as e:
            print(f"Failed to toggle strategy auto_recreate config: {e}")
            return False

    def toggle_strategy_auto_fix(self, strategy_name: str, symbol: str, auto_fix: bool) -> bool:
        """
        Updates the auto_fix status of a specific contract in a strategy,
        persisting it to strategies.yaml while preserving comments.
        Returns True if updated successfully, False if contract/strategy not found.
        """
        try:
            import ruamel.yaml
        except ImportError:
            raise ImportError("ruamel.yaml is required for modifying config while preserving comments")

        yaml_parser = ruamel.yaml.YAML()
        yaml_parser.preserve_quotes = True
        
        try:
            with open(self.strategies_path, 'r') as f:
                data = yaml_parser.load(f)
            
            strat_node = data.get('strategies', {}).get(strategy_name)
            if not strat_node:
                return False
                
            contracts = strat_node.get('contracts', [])
            found = False
            for contract in contracts:
                if contract.get('symbol') == symbol:
                    contract['auto_fix'] = auto_fix
                    found = True
                    break
                    
            if not found:
                return False
                
            with open(self.strategies_path, 'w') as f:
                yaml_parser.dump(data, f)
            
            if strategy_name in self.strategies_data['strategies']:
                mem_strat = self.strategies_data['strategies'][strategy_name]
                for mem_c in mem_strat.get('contracts', []):
                    if mem_c.get('symbol') == symbol:
                        mem_c['auto_fix'] = auto_fix
            
            return True
            
        except Exception as e:
            print(f"Failed to toggle strategy auto_fix config: {e}")
            return False
