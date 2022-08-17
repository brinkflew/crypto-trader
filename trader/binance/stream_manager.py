"""
Binance stream manager
"""

import sys
import time
import threading

from binance.exceptions import BinanceAPIException, BinanceRequestException
from unicorn_binance_websocket_api import BinanceWebSocketApiManager

from trader.logger import logger, term, pretty
from trader.binance.order import BinanceOrder
from trader.binance.order_guard import BinanceOrderGuard


class BinanceStreamManager:

    def __init__(self, cache, config, client):
        self.cache = cache
        self.logger = logger
        self.bsm = BinanceWebSocketApiManager(
            output_default="UnicornFy",
            enable_stream_signal_buffer=True,
            exchange=f"binance.{config.BINANCE_TLD}",
        )

        self.bsm.create_stream(
            ["arr"],
            ["!miniTicker"],
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_API_SECRET,
        )

        self.bsm.create_stream(
            ["arr"],
            ["!userData"],
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_API_SECRET,
        )

        self.client = client
        self.pending_orders = set()
        self.pending_orders_mutex = threading.Lock()
        self._processorThread = threading.Thread(target=self._stream_processor)
        self._processorThread.start()

    def acquire_order_guard(self):
        return BinanceOrderGuard(self.pending_orders, self.pending_orders_mutex)

    def _fetch_pending_orders(self):
        with self.pending_orders_mutex:
            pending_orders = self.pending_orders.copy()

        for (symbol, order_id) in pending_orders:
            order = None

            while True:
                try:
                    order = self.client.get_order(symbol=symbol, orderId=order_id)
                except (BinanceRequestException, BinanceAPIException) as e:
                    logger.exception(e)
                if order is not None:
                    break
                time.sleep(1)

            fake_report = {
                "symbol": order["symbol"],
                "side": order["side"],
                "order_type": order["type"],
                "order_id": order["orderId"],
                "cumulative_quote_asset_transacted_quantity": float(order["cummulativeQuoteQty"]),
                "current_order_status": order["status"],
                "order_price": float(order["price"]),
                "transaction_time": order["time"],
            }

            logger.debug(f"Pending order <{order_id}> for {term.yellow_bold(symbol)}:\n{pretty(fake_report)}")
            self.cache.orders[fake_report["order_id"]] = BinanceOrder(fake_report)

    def _invalidate_balances(self):
        with self.cache.open_balances() as balances:
            balances.clear()

    def _stream_processor(self):
        while True:
            if self.bsm.is_manager_stopping():
                sys.exit()

            stream_signal = self.bsm.pop_stream_signal_from_stream_signal_buffer()
            stream_data = self.bsm.pop_stream_data_from_stream_buffer()

            if stream_signal is not False:
                signal_type = stream_signal["type"]
                stream_id = stream_signal["stream_id"]

                if signal_type == "CONNECT":
                    stream_info = self.bsm.get_stream_info(stream_id)

                    if "!userData" in stream_info["markets"]:
                        logger.debug(f"Received {signal_type} signal for UserData")
                        self._fetch_pending_orders()
                        self._invalidate_balances()

            if stream_data is not False:
                self._process_stream_data(stream_data)

            if stream_data is False and stream_signal is False:
                time.sleep(0.01)

    def _process_stream_data(self, stream_data):
        event_type = stream_data["event_type"]

        if event_type == "executionReport":  # !userData
            logger.debug(f"Execution report:\n{pretty(stream_data)}")
            order = BinanceOrder(stream_data)
            self.cache.orders[order.id] = order

        elif event_type == "balanceUpdate":  # !userData
            logger.debug(f"Balance update:\n{pretty(stream_data)}")

            with self.cache.open_balances() as balances:
                asset = stream_data["asset"]

                if asset in balances:
                    del balances[stream_data["asset"]]

        elif event_type in ("outboundAccountPosition", "outboundAccountInfo"):  # !userData
            logger.debug(f"{event_type}:\n{pretty(stream_data)}")

            with self.cache.open_balances() as balances:
                for bal in stream_data["balances"]:
                    balances[bal["asset"]] = float(bal["free"])

        elif event_type == "24hrMiniTicker":
            for event in stream_data["data"]:
                self.cache.ticker_values[event["symbol"]] = float(event["close_price"])

        else:
            logger.error(f"Unknown event type: {event_type}\n{pretty(stream_data)}")

    def close(self):
        self.bsm.stop_manager_with_all_streams()
