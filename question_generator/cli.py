from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .runner import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="question_generator",
        description="Generate labeled questions from each generated scene's twin.jsonl file.",
    )
    parser.add_argument("-c", "--config", required=True, help="Path to the question generator YAML config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args.config)
    except (OSError, ValueError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Scenes scanned: {result.scene_count}")
    for category in result.categories:
        print(f"{category.category}: {category.generated_count} questions -> {category.output_file}")
        print(f"{category.category}: distributed to {len(category.scene_output_files)} scene file(s)")
        for count in category.counts:
            if count.complete:
                continue
            print(
                f"available: {count.template_id} label={count.target_label} "
                f"target={count.requested} generated={count.generated}"
            )
    return 0
