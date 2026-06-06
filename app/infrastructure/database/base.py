"""
Базовый класс SQLAlchemy DeclarativeBase для всех ORM-моделей.
Импортируется единожды — все модели регистрируются автоматически через наследование.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
