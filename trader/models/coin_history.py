"""
History of coin holdings
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from trader.models import Base, Coin


class CoinHistory(Base):
    __tablename__ = "coin_history"

    id = Column(Integer, primary_key=True)

    coins_id = Column(String, ForeignKey("coin.symbol"))
    coin = relationship(Coin)

    datetime = Column(DateTime)

    def __init__(self, coin: Coin):
        self.coin = coin
        self.datetime = datetime.utcnow()

    def info(self):
        return {
            "datetime": self.datetime.isoformat(),
            "coin": self.coin.info(),
        }
