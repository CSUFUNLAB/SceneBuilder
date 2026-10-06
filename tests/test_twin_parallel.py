from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import main


class TwinParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input_root = self.root / "input"
        self.output_root = self.root / "scenes"
        self.ns3_root = self.root / "ns3"
        self.ns3_root.mkdir()
        self.log = self.ns3_root / "calls.jsonl"
        self.scenes = []
        for index in range(6):
            scene = self.input_root / f"scene{index}"
            scene.mkdir(parents=True)
            for name in (*main.REQUIRED_BASE_SCENE_FILES, "channels.csv", "metadata.json"):
                (scene / name).write_text("{}")
            self.scenes.append(scene)
        launcher = self.ns3_root / "ns3"
        launcher.write_text(f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import shlex
import sys
import time

def log(event, scene=""):
    fd = os.open("calls.jsonl", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.write(fd, (json.dumps({"event": event, "scene": scene}) + "\\n").encode())
    os.close(fd)

if sys.argv[1] == "build":
    log("build")
    sys.exit(9 if Path("build_fail").exists() else 0)
assert sys.argv[-1] == "--no-build", sys.argv
args = dict(part[2:].split("=", 1) for part in shlex.split(sys.argv[2])[1:])
scene = Path(args["scene"]).name
result = Path(args["result"])
log("start", scene)
failure = scene == "scene0" and Path("fail").exists()
time.sleep(0.05 if failure else 0.2)
result.write_text(scene)
labels = result.with_name(result.stem + "_labels.jsonl")
if not Path("missing_labels").exists():
    labels.write_text(scene)
print("NS3_PROGRESS sim_time=1 stop_time=1 events=10", flush=True)
log("end", scene)
sys.exit(7 if failure else 0)
''')
        launcher.chmod(0o755)

    def run_twins(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main.run_twins(self.input_root, ns3_root=self.ns3_root, **kwargs)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_parallel_process_limit_and_output_isolation(self):
        result = self.run_twins(threads=2)
        self.assertTrue(result.complete)
        self.assertEqual(result.generated_files, tuple(self.output_root / f"{s.name}.jsonl" for s in self.scenes))
        active = peak = 0
        calls = self.calls()
        self.assertEqual(calls[0]["event"], "build")
        self.assertEqual(sum(c["event"] == "build" for c in calls), 1)
        for call in calls:
            active += {"start": 1, "end": -1}.get(call["event"], 0)
            peak = max(peak, active)
        self.assertEqual((peak, active), (2, 0))
        for scene, twin in zip(self.scenes, result.generated_files):
            self.assertEqual(twin.read_text(), scene.name)
            self.assertEqual((scene / "labels.jsonl").read_text(), scene.name)
        self.assertFalse(list(self.output_root.glob("*_labels.jsonl")))

    def test_failure_stops_pending_scenes_and_cleans_partial_output(self):
        (self.ns3_root / "fail").touch()
        result = self.run_twins(threads=2)
        self.assertEqual(result.failures, (("scene0 group=0", 7),))
        started = {c["scene"] for c in self.calls() if c["event"] == "start"}
        self.assertLessEqual(started, {"scene0", "scene1"})
        self.assertFalse((self.output_root / "scene0.jsonl").exists())
        self.assertFalse((self.output_root / "scene0_labels.jsonl").exists())
        self.assertEqual(len(result.generated_files), len(started) - 1)

    def test_continue_on_error_runs_remaining_scenes(self):
        (self.ns3_root / "fail").touch()
        result = self.run_twins(threads=3, continue_on_error=True, no_build=True)
        self.assertEqual(len(result.generated_files), 5)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(sum(c["event"] == "start" for c in self.calls()), 6)
        self.assertFalse(any(c["event"] == "build" for c in self.calls()))

    def test_missing_labels_is_failure(self):
        (self.ns3_root / "missing_labels").touch()
        result = self.run_twins(threads=2, continue_on_error=True)
        self.assertEqual(len(result.failures), 6)
        self.assertEqual(result.generated_files, ())
        self.assertFalse(list(self.output_root.iterdir()))

    def test_default_is_serial(self):
        result = self.run_twins()
        self.assertTrue(result.complete)
        self.assertEqual([c["event"] for c in self.calls()], ["build"] + ["start", "end"] * 6)

    def test_dry_run_does_not_start_processes_or_write_outputs(self):
        result = self.run_twins(threads=4, dry_run=True)
        self.assertEqual(len(result.generated_files), 6)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.output_root.exists())

    def test_build_failure_does_not_start_scenes(self):
        (self.ns3_root / "build_fail").touch()
        result = self.run_twins(threads=3)
        self.assertEqual(result.failures, (("build", 9),))
        self.assertEqual(len(self.calls()), 1)

    def test_threads_validation_and_cli(self):
        parser = main.build_parser()
        for category in ("origin", "evolution", "optimization"):
            self.assertEqual(parser.parse_args(["twin", "-t", category, "--threads", "4"]).threads, 4)
        for value in ("0", "-2", "1.5"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["twin", "-t", "origin", "--threads", value])
        for value in (0, -2, 1.5, True):
            with self.assertRaisesRegex(ValueError, "positive integer"):
                self.run_twins(threads=value)

    def test_duplicate_scene_names_rejected_before_build(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            self.run_twins(threads=2, scenes=[self.scenes[0], self.scenes[0]])
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
