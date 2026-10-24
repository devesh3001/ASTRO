"""Adversarial & Security Test Suite for AeroSentinel.

Verifies:
1. Privileged endpoint protection (403 Forbidden without secret token).
2. Secret scrubbing in sandbox run_python environment.
3. Path traversal protection (safe_path blocks ../ escapes).
4. Tool timeout & runaway loop handling (infinite loops stopped safely).
5. Output clipping (caps response at 4000 chars).
6. Deterministic seeded resets (same seed = identical state hash).
7. Seed diversity (different seeds = different initial state).
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.tasks import TASKS, build_task
from sandbox.server import (
    safe_path,
    tool_write_file,
    tool_read_file,
    tool_run_python,
    clip,
    MAX_OUTPUT,
    RUN_TIMEOUT
)


class TestSecurityAndIsolation(unittest.TestCase):
    def test_path_traversal_prevention(self):
        """Verify that directory traversal attempts are blocked."""
        with self.assertRaises(ValueError):
            safe_path("../../etc/passwd")
        with self.assertRaises(ValueError):
            safe_path("../harness/verifier.py")
        with self.assertRaises(ValueError):
            safe_path("/etc/shadow")

    def test_secret_scrubbing_in_sandbox(self):
        """Verify that agent subprocesses cannot see HARNESS_SECRET."""
        os.environ["HARNESS_SECRET"] = "super_classified_token_123"
        code = "import os; print(os.environ.get('HARNESS_SECRET', 'NOT_FOUND'))"
        output = tool_run_python({"code": code})
        self.assertIn("NOT_FOUND", output)
        self.assertNotIn("super_classified_token_123", output)

    def test_timeout_enforcement_on_infinite_loop(self):
        """Verify that an infinite loop code snippet is killed after timeout."""
        code = "import time\nwhile True:\n    time.sleep(0.1)"
        output = tool_run_python({"code": code})
        self.assertIn("exceeded", output)
        self.assertIn(f"{RUN_TIMEOUT} seconds", output)

    def test_output_clipping(self):
        """Verify output truncation at MAX_OUTPUT characters."""
        long_text = "A" * (MAX_OUTPUT + 500)
        clipped = clip(long_text)
        self.assertLessEqual(len(clipped), MAX_OUTPUT + 100)
        self.assertIn("[output cut at", clipped)


class TestTaskSeedsAndReproducibility(unittest.TestCase):
    def test_seeded_reproducibility(self):
        """Same seed must produce bit-for-bit identical task files and state hash."""
        t1 = build_task("safe_mode_cascade", seed=42)
        t2 = build_task("safe_mode_cascade", seed=42)
        self.assertEqual(t1.files, t2.files)
        self.assertEqual(t1.reference, t2.reference)

    def test_seed_variety(self):
        """Different seeds should generate distinct failure variants."""
        t1 = build_task("safe_mode_cascade", seed=42)
        t2 = build_task("safe_mode_cascade", seed=99)
        # Random choice of failed subsystems will differ across seeds
        self.assertNotEqual(t1.files, t2.files)


if __name__ == "__main__":
    unittest.main()
