from abc import ABC, abstractmethod
from typing import Any  # or import your Connection type

class Database(ABC):
    @abstractmethod
    def connect(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def execute_query(self, query: str) -> Any:
        raise NotImplementedError
