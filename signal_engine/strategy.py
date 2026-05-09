from abc import ABC, abstractmethod

class Strategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signal(self, tick: dict) -> dict | None:
        pass

class EmaCrossoverStrategy(Strategy):
    def __init__(self):
        super().__init__("EMA Crossover")
        # Dummy implementation for EMA per symbol
        self.history = {}

    def generate_signal(self, tick: dict) -> dict | None:
        symbol = tick["symbol"]
        if symbol not in self.history:
            self.history[symbol] = []

        self.history[symbol].append(tick["price"])
        if len(self.history[symbol]) > 20:
            self.history[symbol].pop(0)

        # Mock signal generation
        if len(self.history[symbol]) == 20:
            avg = sum(self.history[symbol]) / len(self.history[symbol])
            if tick["price"] > avg * 1.01:
                return {"type": "BUY", "symbol": tick["symbol"], "price": tick["price"], "strategy": self.name}
            elif tick["price"] < avg * 0.99:
                return {"type": "SELL", "symbol": tick["symbol"], "price": tick["price"], "strategy": self.name}
        return None
