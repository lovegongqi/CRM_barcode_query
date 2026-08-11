"""Tests for installation-only service order selection."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


class ServiceOrderPriorityTest(unittest.TestCase):
    def test_install_preferred_even_when_maintenance_is_newer(self):
        fields = {
            "sr2": [
                {"servno1": "FWD202310080064", "typestr1": "安装", "servdate1": "2023-10-08"},
                {"servno1": "FWD202607100083", "typestr1": "维修保养", "servdate1": "2026-07-10"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertIsNotNone(result)
        self.assertEqual(result["service_no"], "FWD202310080064")

    def test_install_newer_also_wins(self):
        fields = {
            "sr2": [
                {"servno1": "FWD202607100083", "typestr1": "安装", "servdate1": "2026-07-10"},
                {"servno1": "FWD202310080064", "typestr1": "维修保养", "servdate1": "2023-10-08"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertEqual(result["service_no"], "FWD202607100083")

    def test_only_maintenance_returns_none(self):
        fields = {
            "sr2": [
                {"servno1": "FWD202310080064", "typestr1": "维修保养", "servdate1": "2023-10-08"},
                {"servno1": "FWD202607100083", "typestr1": "维修保养", "servdate1": "2026-07-10"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertIsNone(result)

    def test_only_installs_picks_latest(self):
        fields = {
            "sr2": [
                {"servno1": "FWD202310080064", "typestr1": "安装", "servdate1": "2023-10-08"},
                {"servno1": "FWD202607100083", "typestr1": "安装", "servdate1": "2026-07-10"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertEqual(result["service_no"], "FWD202607100083")

    def test_empty_returns_none(self):
        self.assertIsNone(app._latest_service_record({"sr2": []}))
        self.assertIsNone(app._latest_service_record({}))

    def test_missing_type_field_returns_none(self):
        fields = {
            "sr2": [
                {"servno1": "FWD202310080064", "servdate1": "2023-10-08"},
                {"servno1": "FWD202607100083", "servdate1": "2026-07-10"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertIsNone(result)

    def test_priority_uses_servtype1_alias(self):
        fields = {
            "sr2": [
                {"servno1": "FWD202310080064", "servtype1": "安装", "servdate1": "2023-10-08"},
                {"servno1": "FWD202607100083", "servtype1": "维修保养", "servdate1": "2026-07-10"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertEqual(result["service_no"], "FWD202310080064")

    def test_bao_yang_maps_to_repair_priority(self):
        # 保养 should not outrank 安装
        fields = {
            "sr2": [
                {"servno1": "FWD202310080064", "typestr1": "安装", "servdate1": "2023-10-08"},
                {"servno1": "FWD202607100083", "typestr1": "保养", "servdate1": "2026-07-10"},
            ]
        }
        result = app._latest_service_record(fields)
        self.assertEqual(result["service_no"], "FWD202310080064")


if __name__ == "__main__":
    unittest.main()
