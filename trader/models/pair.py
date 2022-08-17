"""
Coin pairs
"""

from sqlalchemy import Column, Float, ForeignKey, Integer, String, func, or_, select
from sqlalchemy.orm import column_property, relationship

from trader.models import Base, Coin


class Pair(Base):
    __tablename__ = "pair"

    id = Column(Integer, primary_key=True)

    from_coin_id = Column(String, ForeignKey("coin.symbol"))
    from_coin = relationship("Coin", foreign_keys=[from_coin_id], lazy="joined")

    to_coin_id = Column(String, ForeignKey("coin.symbol"))
    to_coin = relationship("Coin", foreign_keys=[to_coin_id], lazy="joined")

    ratio = Column(Float)

    enabled = column_property(
        select([func.count(Coin.symbol) == 2])
        .where(or_(Coin.symbol == from_coin_id, Coin.symbol == to_coin_id))
        .where(Coin.enabled.is_(True))
        .scalar_subquery()  # type: ignore
    )

    def __init__(self, from_coin: Coin, to_coin: Coin, ratio=None):
        self.from_coin = from_coin
        self.to_coin = to_coin
        self.ratio = ratio

    def __repr__(self):
        return f"{self.from_coin.symbol}{self.to_coin.symbol}"

    def __format__(self):
        return self.__repr__()

    def __str__(self):
        return self.__repr__()

    def info(self):
        return {
            "from_coin": self.from_coin.info(),
            "to_coin": self.to_coin.info(),
            "ratio": self.ratio,
        }
