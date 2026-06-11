from __future__ import annotations

import hashlib
import random
from typing import Mapping

from ..rng import RandomManager


def pick_state(
    probabilities: Mapping[str, object],
    default_state: str,
    rng: RandomManager,
    key: str | None = None,
) -> str:
    if not probabilities:
        return default_state

    states = [str(state) for state in probabilities.keys()]
    weights = [float(weight) for weight in probabilities.values()]
    if key is None:
        return str(rng.weighted_choice(states, weights))

    if any(weight < 0 for weight in weights):
        raise ValueError("State weights must be non-negative")
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("At least one state weight must be positive")

    seed_material = f"{rng.seed}:{key}".encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    threshold = random.Random(seed).uniform(0.0, total)

    cumulative = 0.0
    for state, weight in zip(states, weights):
        cumulative += float(weight)
        if threshold <= cumulative:
            return state

    return states[-1]
