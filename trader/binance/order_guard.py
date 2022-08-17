"""
Binance order guard
"""


class BinanceOrderGuard:
    def __init__(self, pending_orders, mutex):
        self.pending_orders = pending_orders
        self.mutex = mutex

        # Lock immediately because BinanceOrderGuard
        # should be entered and put tag that shouldn't be missed
        self.mutex.acquire()

        self.tag = None

    def set_order(self, origin_symbol: str, target_symbol: str, order_id: int):
        self.tag = (origin_symbol + target_symbol, order_id)

    def __enter__(self):
        try:
            if self.tag is None:
                raise Exception("BinanceOrderGuard wasn't properly set")
            self.pending_orders.add(self.tag)
        finally:
            self.mutex.release()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.pending_orders.remove(self.tag)
