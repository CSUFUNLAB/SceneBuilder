"""Deterministic scene-disjoint splitting with exact template/label quotas."""

from collections import Counter, defaultdict
import random
from typing import Any, Callable


def split_scene_groups(
    records: list[dict[str, Any]],
    *,
    train_ratio: float,
    seed: int,
    enum_templates: set[str],
    scene_id: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not records:
        return [], []
    rng = random.Random(seed)
    strata = [
        (str(r["template_id"]), str(r["label"]) if r["template_id"] in enum_templates else "")
        for r in records
    ]
    keys = sorted(set(strata))
    key_index = {key: i for i, key in enumerate(keys)}
    totals = Counter(strata)
    targets = tuple(
        min(totals[key] - 1, max(1, round(totals[key] * train_ratio)))
        if totals[key] > 1 else totals[key]
        for key in keys
    )
    groups: dict[str, list[int]] = defaultdict(list)
    for i, record in enumerate(records):
        groups[scene_id(record)].append(i)
    names = sorted(groups)
    rng.shuffle(names)
    single = [[] for _ in keys]
    cross = []
    for name in names:
        counts = Counter(key_index[strata[i]] for i in groups[name])
        if len(counts) == 1:
            key, count = next(iter(counts.items()))
            single[key].append((name, count))
        else:
            cross.append((name, tuple(counts.get(i, 0) for i in range(len(keys)))))
    cross.sort(key=lambda item: -sum(item[1]))

    # Whole-scene subset sums handle groups belonging to just one stratum.
    choices: list[dict[int, tuple[str, ...]]] = []
    for key, items in enumerate(single):
        reachable: dict[int, tuple[str, ...]] = {0: ()}
        for name, size in items:
            for count, selected in list(reachable.items()):
                new_count = count + size
                if new_count <= targets[key] and new_count not in reachable:
                    reachable[new_count] = selected + (name,)
        choices.append(reachable)

    # Only scenes linking multiple strata require joint assignment.
    suffix = [(0,) * len(keys) for _ in range(len(cross) + 1)]
    for i in range(len(cross) - 1, -1, -1):
        suffix[i] = tuple(a + b for a, b in zip(cross[i][1], suffix[i + 1]))
    failed = set()
    visited = 0

    def search(position: int, used: tuple[int, ...]) -> set[str] | None:
        nonlocal visited
        state = (position, used)
        if state in failed:
            return None
        visited += 1
        if visited > 200_000:
            raise ValueError(
                "Scene-disjoint split search exceeded its limit. Reduce shared-scene "
                "questions or generate more independent scenes; no output was published."
            )
        for k, count in enumerate(used):
            remaining = targets[k] - count
            if remaining < 0 or not any(
                0 <= remaining - value <= suffix[position][k] for value in choices[k]
            ):
                failed.add(state)
                return None
        if position == len(cross):
            selected = set()
            for k, count in enumerate(used):
                selected.update(choices[k][targets[k] - count])
            return selected
        name, counts = cross[position]
        # Try the split with the greater relative deficit first.
        need = sum(targets[k] - used[k] for k, n in enumerate(counts) if n)
        available = sum(suffix[position][k] + max(choices[k]) for k, n in enumerate(counts) if n)
        options = (True, False) if need >= available * 0.5 else (False, True)
        for take in options:
            updated = tuple(a + b for a, b in zip(used, counts)) if take else used
            selected = search(position + 1, updated)
            if selected is not None:
                if take:
                    selected.add(name)
                return selected
        failed.add(state)
        return None

    train_scenes = search(0, (0,) * len(keys))
    if train_scenes is None:
        raise ValueError(
            "Cannot meet template/label quotas without splitting a scene across train "
            "and test. Generate more independent scenes or adjust the question quotas."
        )
    train = [r for r in records if scene_id(r) in train_scenes]
    test = [r for r in records if scene_id(r) not in train_scenes]
    return train, test
