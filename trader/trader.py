"""
Automagic trader
"""

import re
import random

from datetime import datetime as _datetime

from trader.logger import logger, discord_logger, term
from trader.models import Coin, CoinValue, Pair
from trader.exceptions import ControlledException


BTC_SYMBOL = "BTC"


class Trader:
    def __init__(self, manager, database, config):
        self.logger = logger
        self.database = database
        self.config = config
        self.manager = manager

    def initialize(self):
        self.initialize_trade_thresholds()
        self.initialize_current_coin()
        self.initialize_starting_balances()

    def initialize_trade_thresholds(self):
        """
        Initialize the buying threshold of all the coins for trading between them
        """

        with self.database.db_session() as session:
            for pair in session.query(Pair).filter(Pair.ratio.is_(None)).all():
                print(str(pair))
                if pair.from_coin.symbol == pair.to_coin.symbol:
                    continue

                if not pair.from_coin.enabled or not pair.to_coin.enabled:
                    continue

                logger.info(f"Initializing pair {term.yellow_bold(str(pair))}")

                from_pair = pair.from_coin + self.config.FIAT_SYMBOL
                from_coin_price = self.manager.get_ticker_price(from_pair)
                if from_coin_price is None:
                    logger.warning(
                        f"{term.yellow_bold(from_pair)} symbol not found, "
                        f"skipping initialization"
                    )
                    continue

                to_pair = pair.to_coin + self.config.FIAT_SYMBOL
                to_coin_price = self.manager.get_ticker_price(to_pair)
                if to_coin_price is None:
                    logger.warning(
                        f"{term.yellow_bold(to_pair)} symbol not found, "
                        f"skipping initialization"
                    )
                    continue

                pair.ratio = from_coin_price / to_coin_price
                logger.over(f"Initialized pair {term.yellow_bold(str(pair))}")

    def initialize_current_coin(self):
        """
        Decide what is the current coin, and set it up in the database
        """
        if self.database.get_current_coin() is None:
            current_coin_symbol = self.config.FIAT_SYMBOL

            if not current_coin_symbol:
                current_coin_symbol = random.choice(self.config.COINS_LIST)

            self.logger.info(f"Setting initial coin to {term.yellow_bold(current_coin_symbol)}")
            self.database.set_current_coin(current_coin_symbol)

            # If the current coin is not in the available coins, sell it for the FIAT coin
            # so that we can start trading
            if current_coin_symbol not in self.config.COINS_LIST:
                current_coin = self.database.get_current_coin()
                self.logger.info(
                    f"Current coin {term.yellow_bold(current_coin_symbol)} is not in the available coins list"
                )

                if not self.logger.ask(f"Do you want to switch to {term.yellow_bold(self.config.FIAT_SYMBOL)}?"):
                    raise ControlledException("User did not want to switch to an available coin")

                self.logger.info(f"Selling {term.yellow_bold(current_coin_symbol)} to begin trading")
                self.manager.sell_alt(current_coin, self.config.FIAT_SYMBOL)

    def initialize_starting_balances(self):
        symbols = {
            self.config.FIAT_SYMBOL,
            BTC_SYMBOL,
            self.config.COIN_SYMBOL,
        }

        values = {symbol: self.manager.collate_coins(symbol) for symbol in symbols}

        with self.manager.cache.starting_balances() as starting_balances:
            starting_balances.update({symbol: value for symbol, value in values.items()})

    def display_balance(self):
        """
        Log the current balance total value in the currently held coin, BTC and the bridge coin.
        """

        # Clear the cached balances once and only once
        with self.manager.cache.open_balances() as open_balances:
            open_balances.clear()

        with self.manager.cache.starting_balances() as starting_balances:
            filtered_coins = ["BTC", self.config.FIAT_SYMBOL, self.config.REPR_SYMBOL]
            values = {}

            for symbol, starting_balance in starting_balances.items():
                if symbol not in filtered_coins:
                    continue

                collated_balance = self.manager.collate_coins(symbol)
                change = '{:+,.2f}%'.format((collated_balance - starting_balance) / collated_balance * 100)

                if re.match(r"^[+-]0\.00%$", change):
                    change = change.replace('-', '+')
                    change_color = term.darkgray(change)
                elif change.startswith('+'):
                    change_color = term.darkolivegreen3(change)
                else:
                    change_color = term.lightcoral(change)

                values[symbol] = {
                    "balance": '{:,.8f}'.format(collated_balance),
                    "change": change,
                    "change_color": change_color,
                }

        current_coin = self.database.get_current_coin()
        current_coin_balance = '{:,.8f}'.format(self.manager.get_currency_balance(current_coin.symbol))
        logger.info(f"Holding {current_coin_balance} {term.yellow_bold(current_coin.symbol)}")

        values = {symbol: values[symbol] for symbol in values}
        balance_align_size = max(len(value["balance"]) for value in values.values())
        symbol_align_size = max(len(symbol) for symbol in values.keys())
        formatted_values = [
            f"{'{:>{align}}'.format(value['balance'], align=balance_align_size + 4)} "
            f"{term.yellow_bold('{:<{align}}'.format(symbol, align=symbol_align_size))} "
            f"{term.darkgray('(')}"
            f"{value['change_color']}"
            f"{term.darkgray(')')}"
            for symbol, value in values.items()
        ]

        logger.info("Collated balance:\n" + "\n".join(formatted_values))

        if logger.discord_enabled:
            formatted_values = "\n".join([
                f"{'{:>{align}}'.format(value['balance'], align=balance_align_size)} "
                f"{'{:<{align}}'.format(symbol, align=symbol_align_size)} "
                f"({value['change']})"
                for symbol, value in values.items()
            ])

            discord_logger.info({
                "description":
                    "Holding:"
                    "\n```\n"
                    f"{'{:>{align}}'.format(current_coin_balance, align=balance_align_size)} "
                    f"{current_coin.symbol}"
                    "\n```\n"
                    "Collated balance:"
                    "\n```\n"
                    f"{formatted_values}"
                    "\n```",
            })
