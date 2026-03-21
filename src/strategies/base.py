from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseStrategy(ABC):
    def __init__(self, name: str, config: Dict[str, Any], connector, state_manager):
        self.name = name
        self.config = config
        self.connector = connector
        self.state_manager = state_manager
        self.enabled = config.get('enabled', False)

    @abstractmethod
    async def start(self):
        """Called when the application starts."""
        pass

    @abstractmethod
    def stop(self):
        """Called when the application stops."""
        pass

    @abstractmethod
    async def on_order_update(self, order_data: Dict[str, Any]):
        """Called when an order update is received."""
        pass

    @abstractmethod
    async def on_execution(self, exec_data: Dict[str, Any]):
        """Called when an execution report is received."""
        pass
