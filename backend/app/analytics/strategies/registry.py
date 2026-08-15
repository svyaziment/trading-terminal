"""Strategy registry - manages available strategy plugins."""

from __future__ import annotations

from typing import Dict, List, Type

from app.analytics.strategies.base import StrategyPlugin


class StrategyRegistry:
    """Registry for strategy plugins."""

    def __init__(self):
        self._plugins: Dict[str, Type[StrategyPlugin]] = {}

    def register(self, name: str, plugin_class: Type[StrategyPlugin]) -> None:
        """Register a strategy plugin."""
        if name in self._plugins:
            raise ValueError(f"Strategy '{name}' already registered")
        if not issubclass(plugin_class, StrategyPlugin):
            raise TypeError(f"{plugin_class} must be a subclass of StrategyPlugin")
        self._plugins[name] = plugin_class

    def get_plugin(self, name: str, config: dict) -> StrategyPlugin:
        """Get strategy plugin instance by name."""
        if name not in self._plugins:
            available = sorted(self._plugins.keys())
            raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
        return self._plugins[name](config)

    def list_plugins(self) -> List[str]:
        """List all registered strategy names."""
        return sorted(self._plugins.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)


_global_registry = StrategyRegistry()


def get_registry() -> StrategyRegistry:
    """Get global strategy registry."""
    return _global_registry


def register_default_strategies() -> None:
    """Register all default strategy plugins."""
    from app.analytics.strategies.levels_reversal import LevelsReversalStrategy

    registry = get_registry()
    if 'levels_reversal' not in registry:
        registry.register('levels_reversal', LevelsReversalStrategy)
