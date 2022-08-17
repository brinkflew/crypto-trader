"""
Binance API manager
"""

import time
import math
import traceback

from cachetools import TTLCache, cached
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

from trader.logger import logger, term, pretty
from trader.models import Coin
from trader.binance.stream_manager import BinanceStreamManager
from trader.binance.cache import BinanceCache

BNB_COIN = "BNB"


class BinanceManager:

    def __init__(self, config, database):
        self.database = database
        self.config = config
        self.logger = logger

        self.client = BinanceClient(
            config.BINANCE_API_KEY,
            config.BINANCE_API_SECRET,
            tld=config.BINANCE_TLD,
        )

        self.cache = BinanceCache()
        self.stream_manager = None
        self.setup_websockets()

    def setup_websockets(self):
        self.stream_manager = BinanceStreamManager(
            self.cache,
            self.config,
            self.client,
        )

    def reconnect(self):
        if isinstance(self.stream_manager, BinanceStreamManager):
            self.stream_manager.close()

        self.client = BinanceClient(
            self.config.BINANCE_API_KEY,
            self.config.BINANCE_API_SECRET,
            tld=self.config.BINANCE_TLD,
        )

        self.setup_websockets()

    def test_connection(self):
        """
        Check if we can access API features that require a valid config
        """
        try:
            self.client.ping()
            self.get_account()
        except BinanceAPIException as exception:
            if not exception.message:
                exception.message = "Couldn't access Binance API - API keys may be wrong or lack sufficient permissions"
            raise Exception(exception.message) from exception

    # @cached(cache=TTLCache(maxsize=1, ttl=43200))
    def get_trade_fees(self):
        return {ticker["symbol"]: float(ticker["takerCommission"]) for ticker in self.client.get_trade_fee()}

    @cached(cache=TTLCache(maxsize=1, ttl=60))
    def get_using_bnb_for_fees(self):
        return self.client.get_bnb_burn_spot_margin()["spotBNBBurn"]

    def get_fee(self, origin_coin, target_coin, selling):
        base_fee = self.get_trade_fees()[origin_coin + target_coin]

        if not self.get_using_bnb_for_fees():
            return base_fee

        # The discount is only applied if we have enough BNB to cover the fee
        amount_trading = (
            self._sell_quantity(origin_coin.symbol, target_coin.symbol)
            if selling
            else self._buy_quantity(origin_coin.symbol, target_coin.symbol)
        )

        fee_amount = amount_trading * base_fee * 0.75

        if origin_coin.symbol == BNB_COIN:
            fee_amount_bnb = fee_amount
        else:
            origin_price = self.get_ticker_price(origin_coin + Coin(BNB_COIN))

            if origin_price is None:
                return base_fee

            fee_amount_bnb = fee_amount * origin_price

        bnb_balance = self.get_currency_balance(BNB_COIN)

        if bnb_balance >= fee_amount_bnb:
            return base_fee * 0.75
        return base_fee

    def get_account(self):
        """
        Get account information
        """
        return self.client.get_account()

    def get_ticker_price(self, ticker_symbol):
        """
        Get ticker price of a specific coin
        """
        price = self.cache.ticker_values.get(ticker_symbol, None)

        if price is None and ticker_symbol not in self.cache.non_existent_tickers:
            self.cache.ticker_values = {
                ticker["symbol"]: float(ticker["price"])
                for ticker in self.client.get_symbol_ticker()
            }
            logger.debug(f"Ticker prices fetched:\n{pretty(self.cache.ticker_values)}")
            price = self.cache.ticker_values.get(ticker_symbol, None)

            if price is None:
                logger.debug(f"Ticker {term.yellow_bold(ticker_symbol)} not found, skipping")
                self.cache.non_existent_tickers.add(ticker_symbol)

        return price

    def get_currency_balance(self, currency_symbol, force=False):
        """
        Get balance of a specific coin
        """
        with self.cache.open_balances() as cache_balances:
            balance = cache_balances.get(currency_symbol, None)

            if force or balance is None:
                cache_balances.clear()
                cache_balances.update({
                    currency_balance["asset"]: float(currency_balance["free"])
                    for currency_balance in self.client.get_account()["balances"]
                })
                logger.debug(f"Balances fetched:\n{pretty(cache_balances)}")

                if currency_symbol not in cache_balances:
                    cache_balances[currency_symbol] = 0.0
                    return 0.0
                return cache_balances.get(currency_symbol, 0.0)
            return balance

    def retry(self, func, *args, **kwargs):
        time.sleep(1)
        attempts = 0

        while attempts < 20:
            try:
                return func(*args, **kwargs)
            except Exception:
                logger.debug(f"Failed to place order, retrying [{attempts}/20]")

                if attempts == 0:
                    logger.warning(traceback.format_exc())

                attempts += 1
        return None

    def get_symbol_filter(self, origin_symbol, target_symbol, filter_type):
        return next(
            _filter
            for _filter in (self.client.get_symbol_info(origin_symbol + target_symbol) or {}).get("filters", {})
            if _filter.get("filterType") == filter_type
        )

    @cached(cache=TTLCache(maxsize=2000, ttl=43200))
    def get_alt_tick(self, origin_symbol: str, target_symbol: str):
        step_size = self.get_symbol_filter(origin_symbol, target_symbol, "LOT_SIZE")["stepSize"]

        if step_size.find("1") == 0:
            return 1 - step_size.find(".")
        return step_size.find("1") - 1

    @cached(cache=TTLCache(maxsize=2000, ttl=43200))
    def get_min_notional(self, origin_symbol, target_symbol):
        return float(self.get_symbol_filter(origin_symbol, target_symbol, "MIN_NOTIONAL")["minNotional"])

    def _wait_for_order(self, order_id, origin_symbol, target_symbol):
        while True:
            order_status = self.cache.orders.get(order_id, None)

            if order_status is not None:
                break

            logger.debug(f"Waiting for creation of order <{order_id}>")
            time.sleep(1)

        logger.debug(f"Order created:\n{pretty(order_status)}")

        while order_status.status != "FILLED":  # type: ignore
            try:
                order_status = self.cache.orders.get(order_id, None)
                assert order_status is not None
                logger.debug(f"Waiting for fulfillment of order <{order_id}>")

                if self._should_cancel_order(order_status):
                    cancel_order = None

                    while cancel_order is None:
                        cancel_order = self.client.cancel_order(
                            symbol=origin_symbol + target_symbol,
                            orderId=order_id,
                        )
                    logger.debug(f"Order <{order_id}> timed out, cancelled")

                    # Sell partially
                    if order_status.status == "PARTIALLY_FILLED" and order_status.side == "BUY":
                        logger.debug("Reselling partially filled amount")

                        order_quantity = self._sell_quantity(origin_symbol, target_symbol)
                        partially_order = None

                        while partially_order is None:
                            partially_order = self.client.order_market_sell(
                                symbol=origin_symbol + target_symbol,
                                quantity=order_quantity,
                            )

                    logger.over("Scouting...")
                    return None

                if order_status.status == "CANCELED":
                    logger.debug(f"Order <{order_id}> canceled")
                    logger.over("Scouting...")
                    return None

                time.sleep(1)
            except BinanceAPIException as e:
                logger.warning(e)
                time.sleep(1)
            except Exception as e:
                logger.exception(e)
                time.sleep(1)

        logger.debug(f"Order filled: {order_status}")
        return order_status

    def wait_for_order(self, order_id, origin_symbol, target_symbol, order_guard):
        with order_guard:
            return self._wait_for_order(order_id, origin_symbol, target_symbol)

    def _should_cancel_order(self, order_status):
        minutes = (time.time() - order_status.time / 1000) / 60
        timeout = 0

        if order_status.side == "SELL":
            timeout = float(self.config.SELL_TIMEOUT)
        else:
            timeout = float(self.config.BUY_TIMEOUT)

        if timeout and minutes > timeout and order_status.status == "NEW":
            return True

        if timeout and minutes > timeout and order_status.status == "PARTIALLY_FILLED":
            if order_status.side == "SELL":
                return True

            if order_status.side == "BUY":
                current_price = self.get_ticker_price(order_status.symbol)

                if float(current_price or 0.0) * (1 - 0.001) > float(order_status.price):
                    return True

        return False

    def buy_alt(self, origin_coin, target_coin):
        return self.retry(self._buy_alt, origin_coin, target_coin)

    def _buy_quantity(self, origin_symbol, target_symbol, target_balance=None, from_coin_price=None):
        target_balance = target_balance or self.get_currency_balance(target_symbol)
        from_coin_price = from_coin_price or self.get_ticker_price(origin_symbol + target_symbol) or 1.0
        origin_tick = self.get_alt_tick(origin_symbol, target_symbol)
        return math.floor(target_balance * 10 ** origin_tick / from_coin_price) / float(10 ** origin_tick)

    def _buy_alt(self, origin_coin: Coin, target_coin: Coin):
        """
        Buy altcoin
        """
        if self.stream_manager is None:
            return None

        trade_log = self.database.start_trade_log(origin_coin, target_coin, False)
        origin_symbol = origin_coin.symbol
        target_symbol = target_coin.symbol

        with self.cache.open_balances() as balances:
            balances.clear()

        origin_balance = self.get_currency_balance(origin_symbol)
        target_balance = self.get_currency_balance(target_symbol)
        from_coin_price = self.get_ticker_price(origin_symbol + target_symbol)

        if from_coin_price is None:
            return None

        order_quantity = self._buy_quantity(origin_symbol, target_symbol, target_balance, from_coin_price)
        logger.debug(
            f"Placing {term.darkolivegreen3_bold('BUY')} order for "
            f"{'{:.8f}'.format(order_quantity)} "
            f"{term.yellow_bold(origin_symbol)} "
            f"{term.darkgray('at')} "
            f"{'{:.8f}'.format(order_quantity * from_coin_price)} "
            f"{term.yellow_bold(target_symbol)} "
        )

        # Try to buy until successful
        order = None
        order_guard = self.stream_manager.acquire_order_guard()

        while order is None:
            try:
                order = self.client.order_limit_buy(
                    symbol=origin_symbol + target_symbol,
                    quantity=order_quantity,
                    price=from_coin_price,
                )
                logger.debug(f"Order:\n{pretty(order)}")
            except BinanceAPIException as e:
                logger.warning(e)
                time.sleep(1)
            except Exception as e:
                logger.exception(e)

        trade_log.set_ordered(origin_balance, target_balance, order_quantity)

        order_guard.set_order(origin_symbol, target_symbol, int(order["orderId"]))
        order = self.wait_for_order(order["orderId"], origin_symbol, target_symbol, order_guard)

        if order is None:
            return None

        logger.info(
            f"{term.darkolivegreen3_bold('BUY')} "
            f"{'{:.8f}'.format(order_quantity)} "
            f"{term.yellow_bold(origin_symbol)} at "
            f"{'{:.8f}'.format(order_quantity * from_coin_price)} "
            f"{term.yellow_bold(target_symbol)} "
        )
        trade_log.set_complete(order.cumulative_quote_qty)
        return order

    def sell_alt(self, origin_coin, target_coin):
        return self.retry(self._sell_alt, origin_coin, target_coin)

    def _sell_quantity(self, origin_symbol, target_symbol, origin_balance=None):
        origin_balance = origin_balance or self.get_currency_balance(origin_symbol)
        origin_tick = self.get_alt_tick(origin_symbol, target_symbol)
        return math.floor(origin_balance * 10 ** origin_tick) / float(10 ** origin_tick)

    def _sell_alt(self, origin_coin, target_coin):
        """
        Sell altcoin
        """
        if self.stream_manager is None:
            return None

        trade_log = self.database.start_trade_log(origin_coin, target_coin, True)
        origin_symbol = origin_coin.symbol
        target_symbol = target_coin.symbol

        with self.cache.open_balances() as balances:
            balances.clear()

        origin_balance = self.get_currency_balance(origin_symbol)
        target_balance = self.get_currency_balance(target_symbol)
        from_coin_price = self.get_ticker_price(origin_symbol + target_symbol)

        if from_coin_price is None:
            return None

        order_quantity = self._sell_quantity(origin_symbol, target_symbol, origin_balance)
        logger.debug(
            f"Placing {term.lightcoral_bold('SELL')} order for "
            f"{'{:.8f}'.format(order_quantity)} "
            f"{term.yellow_bold(origin_symbol)} at "
            f"{'{:.8f}'.format(order_quantity * from_coin_price)} "
            f"{term.yellow_bold(target_symbol)} "
        )

        order = None
        order_guard = self.stream_manager.acquire_order_guard()

        while order is None:
            # Should sell at calculated price to avoid lost coin
            order = self.client.order_limit_sell(
                symbol=origin_symbol + target_symbol,
                quantity=order_quantity,
                price=from_coin_price,
            )

        logger.debug(f"Order:\n{pretty(order)}")
        trade_log.set_ordered(origin_balance, target_balance, order_quantity)
        order_guard.set_order(origin_symbol, target_symbol, int(order["orderId"]))
        order = self.wait_for_order(order["orderId"], origin_symbol, target_symbol, order_guard)

        if order is None:
            return None

        new_balance = self.get_currency_balance(origin_symbol)

        while new_balance >= origin_balance:
            new_balance = self.get_currency_balance(origin_symbol, True)

        logger.info(
            f"{term.lightcoral_bold('SELL')} "
            f"{'{:.8f}'.format(order_quantity)} "
            f"{term.yellow_bold(origin_symbol)} at "
            f"{'{:.8f}'.format(order_quantity * from_coin_price)} "
            f"{term.yellow_bold(target_symbol)} "
        )

        trade_log.set_complete(order.cumulative_quote_qty)
        return order

    def collate_coins(self, target_symbol):
        total = .0
        enabled_symbols = {coin.symbol for coin in self.database.get_coins(only_enabled=True)}
        enabled_symbols.add(self.config.FIAT_COIN)

        for symbol in enabled_symbols:
            balance = self.get_currency_balance(symbol)

            if symbol == target_symbol:
                total += balance
                continue

            price = self.get_ticker_price(target_symbol + symbol)

            if price is not None:
                total += balance / price
                continue

            price = self.get_ticker_price(symbol + target_symbol)

            if price is not None:
                total += balance * price
                continue

        return total
