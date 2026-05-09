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
        # Dummy implementation for EMA
        self.history = []

    def generate_signal(self, tick: dict) -> dict | None:
        self.history.append(tick["price"])
        if len(self.history) > 20:
            self.history.pop(0)

        # Mock signal generation
        if len(self.history) == 20:
            avg = sum(self.history) / len(self.history)
            if tick["price"] > avg * 1.01:
                return {"type": "BUY", "symbol": tick["symbol"], "price": tick["price"], "strategy": self.name}
            elif tick["price"] < avg * 0.99:
                return {"type": "SELL", "symbol": tick["symbol"], "price": tick["price"], "strategy": self.name}
        return None
