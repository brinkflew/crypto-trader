"""
Let's go tradin'
"""

import os
import time

from signal import SIGINT, SIGTERM
from urllib3.exceptions import ReadTimeoutError
from requests.exceptions import ReadTimeout
from binance.exceptions import BinanceAPIException

from trader import Config, Database, Scheduler, Trader
from trader.logger import logger
from trader.binance import BinanceManager
from trader.exceptions import ControlledException


def main():
    logger.over("Starting process...")

    config = Config()
    database = Database(config)

    logger.over("Setting up Binance API manager...")
    manager = BinanceManager(config, database)

    logger.over("Testing connection to Binance...")
    manager.test_connection()

    logger.over("Setting up database...")
    database.create_database()
    database.set_coins([
        config.FIAT_SYMBOL,
        config.COIN_SYMBOL,
        config.REPR_SYMBOL,
    ])

    logger.over("Initializing trader...")
    trader = Trader(manager, database, config)
    trader.initialize()

    logger.over("Fetching balance...")
    trader.display_balance()

    schedule = Scheduler()
    # schedule.every(config.SLEEP_TIME).seconds.do(trader.scout).tag("scout")
    # schedule.every(1).minutes.do(trader.update_values).tag("update value history")
    # schedule.every(1).minutes.do(database.prune_scout_history).tag("prune scout history")
    # schedule.every(1).hours.do(database.prune_value_history).tag("prune value history")
    # schedule.every(1).days.at('07:00:00').do(trader.display_balance).tag("display balance")
    # schedule.every(1).days.at('19:00:00').do(trader.display_balance).tag("display balance")

    try:
        reconnection_attempts = 0

        while True:
            try:
                schedule.run_pending()
                time.sleep(1)

            except (ReadTimeoutError, ReadTimeout):
                logger.warning(f"Connection to API manager timed out")

                reconnection_attempts += 1
                attempts_format = (
                    f"[{reconnection_attempts}/"
                    f"{'unlimited' if config.BINANCE_RETRIES_UNLIMITED else config.BINANCE_RETRIES}]"
                )

                if config.BINANCE_RETRIES_UNLIMITED or reconnection_attempts < config.BINANCE_RETRIES:
                    logger.over(f"Reconnecting to Binance API manager {attempts_format}")
                    manager.reconnect()
                    manager.test_connection()
                    logger.success(f"Reconnected to Binance API manager {attempts_format}")
                else:
                    raise ControlledException(f"Maximum reconnection attempts reached {attempts_format}")
    finally:
        if manager.stream_manager:
            manager.stream_manager.close()


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("", end="\r")  # Clear the last line of extraneous characters (i.e. ^C)
        logger.warning(f"Received interrupt signal ({SIGINT}), exiting...")

    except ControlledException as e:
        logger.error(e)

    except Exception as e:
        logger.critical(e, exc_info=True)

    finally:
        os.kill(os.getpid(), SIGTERM)
