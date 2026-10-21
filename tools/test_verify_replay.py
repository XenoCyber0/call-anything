#!/usr/bin/env python3
"""Tests for the replay verifier's comparison logic.

These are pure-function tests -- they do not need a live server.
"""
from __future__ import annotations

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

from verify_replay import comparable, _is_error_evidence  # noqa: E402


class TestComparable(unittest.TestCase):
    def test_identical_json_matches(self):
        a = '{"result": {"items": [], "total": 0}}'
        b = '{"result": {"items": [], "total": 0}}'
        self.assertEqual(comparable(a), comparable(b))

    def test_numeric_drift_does_not_break_the_match(self):
        """A counter that changed between runs must still match."""
        a = '{"result": {"total": 0, "items": []}}'
        b = '{"result": {"total": 7, "items": []}}'
        self.assertEqual(comparable(a), comparable(b))

    def test_null_versus_value_is_a_real_change(self):
        """null -> 20 is a semantic change, not numeric drift."""
        a = '{"result": {"next_offset": null}}'
        b = '{"result": {"next_offset": 20}}'
        self.assertNotEqual(comparable(a), comparable(b))

    def test_key_order_does_not_matter(self):
        a = '{"result": {"a": 1, "b": 2}}'
        b = '{"result": {"b": 2, "a": 1}}'
        self.assertEqual(comparable(a), comparable(b))

    def test_different_shape_does_not_match(self):
        a = '{"result": {"total": 0}}'
        b = '{"result": {"items": []}}'
        self.assertNotEqual(comparable(a), comparable(b))

    def test_type_change_is_detected(self):
        a = '{"result": {"value": "x"}}'
        b = '{"result": {"value": 3}}'
        self.assertNotEqual(comparable(a), comparable(b))

    def test_missing_key_is_detected(self):
        a = '{"result": {"a": 1, "b": 2}}'
        b = '{"result": {"a": 1}}'
        self.assertNotEqual(comparable(a), comparable(b))

    def test_non_json_falls_back_to_text_digest(self):
        a = "plain text output with 42"
        b = "plain text output with 99"
        self.assertEqual(comparable(a), comparable(b))
        c = "completely different"
        self.assertNotEqual(comparable(a), comparable(c))

    def test_error_evidence_detected(self):
        self.assertTrue(_is_error_evidence('{"error": {"code": "x"}}'))
        self.assertFalse(_is_error_evidence('{"result": {"ok": 1}}'))


if __name__ == "__main__":
    unittest.main(verbosity=2)
