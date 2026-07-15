from __future__ import annotations

import hashlib
import random
from typing import Iterable, Sequence, TypeVar

import numpy as np

T = TypeVar("T")


class RandomManager:
    """Unified random manager for deterministic generation."""

    def __init__(self, seed: int) -> None:
        self.seed = int(seed)
        self._py = random.Random(self.seed)
        self._np = np.random.default_rng(self.seed)

    def random(self) -> float:
        return self._py.random()

    def probability(self, p: float) -> bool:
        return self.random() < float(p)

    def fork(self, namespace: str) -> "RandomManager":
        seed_material = f"{self.seed}:{namespace}".encode("utf-8")
        derived_seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        return RandomManager(derived_seed)

    def randint(self, a: int, b: int) -> int:
        return self._py.randint(int(a), int(b))

    def uniform(self, a: float, b: float) -> float:
        return self._py.uniform(float(a), float(b))

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise ValueError("Cannot choose from an empty sequence")
        return seq[self._py.randrange(0, len(seq))]

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        return self._py.sample(seq, k)

    def weighted_choice(self, items: Sequence[T], weights: Sequence[float]) -> T:
        if len(items) != len(weights) or not items:
            raise ValueError("items and weights must have same non-zero length")
        if any(w < 0 for w in weights):
            raise ValueError("Weights must be non-negative")

        total = float(sum(weights))
        if total <= 0:
            raise ValueError("At least one weight must be positive")

        threshold = self.uniform(0.0, total)
        cumulative = 0.0
        for item, weight in zip(items, weights):
            cumulative += float(weight)
            if threshold <= cumulative:
                return item

        return items[-1]

    def np_uniform(self, low: float, high: float, size: int | tuple[int, ...]) -> np.ndarray:
        return self._np.uniform(low=float(low), high=float(high), size=size)

    def np_exponential(self, scale: float, size: int | tuple[int, ...]) -> np.ndarray:
        return self._np.exponential(scale=float(scale), size=size)

    def np_random(self, size: int | tuple[int, ...]) -> np.ndarray:
        return self._np.random(size=size)

    def np_integers(self, low: int, high: int, size: int | tuple[int, ...]) -> np.ndarray:
        return self._np.integers(low=low, high=high, size=size)

    def shuffled(self, values: Iterable[T]) -> list[T]:
        result = list(values)
        self._py.shuffle(result)
        return result
