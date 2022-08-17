"""
Database engine
"""

import os
import time

from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, scoped_session, sessionmaker
# from socketIO_client import SocketIO as SocketIOClient
from trader import Config
from trader.logger import logger
from trader.models import *

DATABASE_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '../.data/trader.sqlite'))
DATABASE_URI = f"sqlite:///{DATABASE_PATH}"


class Database:
    def __init__(self, config, uri=DATABASE_URI):
        self.logger = logger
        self.config = config

        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)

        self.engine = create_engine(uri)
        self.SessionMaker = sessionmaker(bind=self.engine)
        # self.socketio_client = SocketIOClient()

    # def socketio_connect(self):
    #     if self.socketio_client.connected and self.socketio_client.namespaces:
    #         return True
    #     try:
    #         if not self.socketio_client.connected:
    #             self.socketio_client.connect("http://api:5123", namespaces=["/backend"])
    #         while not self.socketio_client.connected or not self.socketio_client.namespaces:
    #             time.sleep(0.1)
    #         return True
    #     except Exception as e:
    #         logger.error(e)
    #         return False

    @contextmanager
    def db_session(self):
        """
        Creates a context with an open SQLAlchemy session
        """
        session = scoped_session(self.SessionMaker)
        yield session
        session.commit()
        session.close()

    def set_coins(self, symbols):
        # Add coins to the database and set them as enabled or not
        with self.db_session() as session:
            # For all the coins in the database, if the symbol no longer appears
            # in the config file, set the coin as disabled
            coins = session.query(Coin).all()

            for coin in filter(lambda c: c.symbol not in symbols, coins):
                coin.enabled = False

            # For all the symbols in the config file, add them to the database
            # if they don't exist
            for symbol in symbols:
                coin = next((coin for coin in coins if coin.symbol == symbol), None)

                if coin is None:
                    session.add(Coin(symbol))
                else:
                    coin.enabled = True

        # For all the combinations of coins in the database, add a pair to the database
        with self.db_session() as session:
            coins = session.query(Coin).filter(Coin.enabled).all()

            for from_coin in coins:
                for to_coin in filter(lambda c: c != from_coin, coins):
                    pair = session.query(Pair).filter(Pair.from_coin == from_coin, Pair.to_coin == to_coin).first()

                    if pair is None:
                        session.add(Pair(from_coin, to_coin))

    def get_coins(self, only_enabled=True):
        with self.db_session() as session:
            if only_enabled:
                coins = session.query(Coin).filter(Coin.enabled).all()
            else:
                coins = session.query(Coin).all()

            session.expunge_all()
            return coins

    def get_coin(self, coin):
        if isinstance(coin, Coin):
            return coin

        with self.db_session() as session:
            coin = session.query(Coin).get(coin)
            session.expunge(coin)
            return coin

    def set_current_coin(self, coin):
        coin = self.get_coin(coin)

        with self.db_session() as session:
            if isinstance(coin, Coin):
                coin = session.merge(coin)

            assert coin is not None
            cc = CoinHistory(coin)
            session.add(cc)
            self.send_update(cc)

    def get_current_coin(self):
        with self.db_session() as session:
            current_coin = session.query(CoinHistory).order_by(CoinHistory.datetime.desc()).first()  # type: ignore

            if current_coin is None:
                return None

            coin = current_coin.coin
            session.expunge(coin)
            return coin

    def get_pair(self, from_coin, to_coin):
        from_coin = self.get_coin(from_coin)
        to_coin = self.get_coin(to_coin)

        with self.db_session() as session:
            pair = session.query(Pair).filter(Pair.from_coin == from_coin, Pair.to_coin == to_coin).first()
            session.expunge(pair)
            return pair

    def get_pairs_from(self, from_coin, only_enabled=True):
        from_coin = self.get_coin(from_coin)

        with self.db_session() as session:
            pairs = session.query(Pair).filter(Pair.from_coin == from_coin)

            if only_enabled:
                pairs = pairs.filter(Pair.enabled.is_(True))

            pairs = pairs.all()
            session.expunge_all()
            return pairs

    def get_pairs(self, only_enabled=True):
        with self.db_session() as session:
            pairs = session.query(Pair)

            if only_enabled:
                pairs = pairs.filter(Pair.enabled.is_(True))

            pairs = pairs.all()
            session.expunge_all()
            return pairs

    def log_scout(
        self,
        pair: Pair,
        target_ratio: float,
        current_coin_price: float,
        other_coin_price: float,
    ):
        with self.db_session() as session:
            pair = session.merge(pair)
            sh = ScoutHistory(pair, target_ratio, current_coin_price, other_coin_price)
            session.add(sh)
            self.send_update(sh)

    def prune_scout_history(self):
        time_diff = datetime.now() - timedelta(hours=self.config.SCOUT_RETENTION_TIME)

        with self.db_session() as session:
            session.query(ScoutHistory).filter(ScoutHistory.datetime < time_diff).delete()

    def prune_value_history(self):
        with self.db_session() as session:
            # Sets the first entry for each coin for each hour as 'hourly'
            hourly_entries = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.strftime("%H", CoinValue.datetime)).all()
            )

            for entry in hourly_entries:
                entry.interval = Interval.HOURLY

            # Sets the first entry for each coin for each day as 'daily'
            daily_entries = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.date(CoinValue.datetime)).all()
            )

            for entry in daily_entries:
                entry.interval = Interval.DAILY

            # Sets the first entry for each coin for each month as 'weekly'
            # (Sunday is the start of the week)
            weekly_entries = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.strftime("%Y-%W", CoinValue.datetime)).all()
            )

            for entry in weekly_entries:
                entry.interval = Interval.WEEKLY

            # The last 24 hours worth of minutely entries will be kept, so
            # count(coins) * 1440 entries
            time_diff = datetime.now() - timedelta(hours=24)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.MINUTELY, CoinValue.datetime < time_diff
            ).delete()

            # The last 28 days worth of hourly entries will be kept, so count(coins) * 672 entries
            time_diff = datetime.now() - timedelta(days=28)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.HOURLY, CoinValue.datetime < time_diff
            ).delete()

            # The last years worth of daily entries will be kept, so count(coins) * 365 entries
            time_diff = datetime.now() - timedelta(days=365)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.DAILY, CoinValue.datetime < time_diff
            ).delete()

            # All weekly entries will be kept forever

    def create_database(self):
        Base.metadata.create_all(self.engine)

    def start_trade_log(self, from_coin: Coin, to_coin: Coin, selling: bool):
        return TradeLog(self, from_coin, to_coin, selling)

    def send_update(self, model):
        return

        if not self.socketio_connect():
            return

        self.socketio_client.emit(
            "update",
            {
                "table": model.__tablename__,
                "data": model.info(),
            },
            namespace="/backend",
        )


class TradeLog:
    def __init__(self, db, from_coin, to_coin, selling):
        self.db = db

        with self.db.db_session() as session:
            from_coin = session.merge(from_coin)
            to_coin = session.merge(to_coin)
            self.trade = TradeHistory(from_coin, to_coin, selling)
            session.add(self.trade)

            # Flush so that SQLAlchemy fills in the id column
            session.flush()
            self.db.send_update(self.trade)

    def set_ordered(self, alt_starting_balance, crypto_starting_balance, alt_trade_amount):
        with self.db.db_session() as session:
            trade = session.merge(self.trade)
            trade.alt_starting_balance = alt_starting_balance
            trade.alt_trade_amount = alt_trade_amount
            trade.crypto_starting_balance = crypto_starting_balance
            trade.state = TradeState.ORDERED
            self.db.send_update(trade)

    def set_complete(self, crypto_trade_amount):
        session: Session
        with self.db.db_session() as session:
            trade = session.merge(self.trade)
            trade.crypto_trade_amount = crypto_trade_amount
            trade.state = TradeState.COMPLETE
            self.db.send_update(trade)


if __name__ == "__main__":
    database = Database(Config())
    database.create_database()
