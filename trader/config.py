"""
Config manager
"""

import os
import configparser

# from trader.logger import logger

CONFIG_SECTION = "trader"
CONFIG_FILE_NAME = "trader.cfg"
CONFIG_FILE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), f"../{CONFIG_FILE_NAME}")
)


class Config:
    def __init__(self):
        config = configparser.ConfigParser()
        config["__default"] = {  # type: ignore
            "binance_tld": "com",
            "binance_retries": 0,
            "fiat_symbol": "USDT",
            "coin_symbol": "BTC",
            "repr_symbol": "USDT",
            "sleep_time": 1,
            "min_profit": 0.2,
            "max_loss": 5.0,
        }

        if os.path.exists(CONFIG_FILE_PATH):
            config.read(CONFIG_FILE_PATH)
        else:
            print(
                f"Configuration file not found ({CONFIG_FILE_NAME}), "
                f"assuming default configuration..."
            )

            config[CONFIG_SECTION] = {}

        def get_option(key):
            return os.environ.get(key.upper()) or config.get(CONFIG_SECTION, key.lower())

        # Binance config
        self.BINANCE_API_KEY = get_option("BINANCE_API_KEY")
        self.BINANCE_API_SECRET = get_option("BINANCE_API_SECRET")
        self.BINANCE_TLD = get_option("BINANCE_TLD")
        self.BINANCE_RETRIES = int(get_option("BINANCE_RETRIES"))
        self.BINANCE_RETRIES_UNLIMITED = not self.BINANCE_RETRIES

        # Coins config
        self.FIAT_SYMBOL = get_option("fiat_symbol")
        self.COIN_SYMBOL = get_option("coin_symbol")
        self.REPR_SYMBOL = get_option("repr_symbol")
        self.COINS_LIST = [self.FIAT_SYMBOL, self.COIN_SYMBOL]

        # Trader config
        self.SLEEP_TIME = int(get_option("sleep_time"))
        self.MIN_PROFIT = float(get_option("min_profit"))
        self.MAX_LOSS = float(get_option("max_loss"))

        # Notifications config
        self.DISCORD_WEBHOOK_URL = get_option("discord_webhook_url")
