"""
Coin value
"""

import enum
from datetime import datetime as _datetime

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from trader.models import Base, Coin


class Interval(enum.Enum):
    MINUTELY = "MINUTELY"
    HOURLY = "HOURLY"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


class CoinValue(Base):
    __tablename__ = "value"

    id = Column(Integer, primary_key=True)

    coin_id = Column(String, ForeignKey("coin.symbol"))
    coin = relationship("Coin")

    balance = Column(Float)
    price_usd = Column(Float)
    price_btc = Column(Float)

    interval = Column(Enum(Interval))

    datetime = Column(DateTime)

    def __init__(
        self,
        coin: Coin,
        balance: float,
        usd_price: float,
        btc_price: float,
        interval=Interval.MINUTELY,
        datetime: _datetime = None,
    ):
        self.coin = coin
        self.balance = balance
        self.usd_price = usd_price
        self.btc_price = btc_price
        self.interval = interval
        self.datetime = datetime or _datetime.now()

    @hybrid_property
    def usd_value(self):  # type: ignore
        if self.usd_price is None:
            return None
        return self.balance * self.usd_price  # type: ignore

    @usd_value.expression
    def usd_value(self):
        return self.balance * self.usd_price  # type: ignore

    @hybrid_property
    def btc_value(self):  # type: ignore
        if self.btc_price is None:
            return None
        return self.balance * self.btc_price  # type: ignore

    @btc_value.expression
    def btc_value(self):
        return self.balance * self.btc_price  # type: ignore

    def info(self):
        return {
            "balance": self.balance,
            "usd_value": self.usd_value,
            "btc_value": self.btc_value,
            "datetime": self.datetime.isoformat(),
        }
