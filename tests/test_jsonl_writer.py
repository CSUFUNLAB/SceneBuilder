import json
from pathlib import Path

from scene_generator.writers.jsonl_writer import write_jsonl


def test_jsonl_writer_preserves_row_fields_as_generated(tmp_path: Path) -> None:
    out = tmp_path / "sparse.jsonl"
    payload = {
        "a": 1,
        "b": "",
        "c": None,
        "d": 0,
        "e": False,
        "f": "x",
    }
    write_jsonl(
        out,
        [payload],
    )

    line = out.read_text(encoding="utf-8").strip()
    row = json.loads(line)

    assert row == payload
