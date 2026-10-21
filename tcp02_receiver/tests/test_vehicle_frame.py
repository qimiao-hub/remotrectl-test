import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SIM_DIR = PROJECT_DIR.parent / "sim_tcp_gui"
for path in (PROJECT_DIR, SIM_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from vehicle_frame import VehicleFrameParser
from vehicle_protocol import REPORT_DEFAULTS, build_vehicle_realtime_frame


class VehicleFrameParserTests(unittest.TestCase):
    def setUp(self):
        self.raw = build_vehicle_realtime_frame("SYZKRMCAB25090501", REPORT_DEFAULTS)

    def test_parses_02_frame_and_validates_bcc(self):
        frames = VehicleFrameParser().feed(self.raw)
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertTrue(frame.is_realtime_report)
        self.assertTrue(frame.bcc_valid)
        self.assertEqual(frame.command_id, 0x02)
        self.assertEqual(frame.payload_length, 40)
        self.assertEqual(len(frame.raw), 65)
        self.assertEqual(frame.raw, self.raw)

    def test_handles_fragmented_frame(self):
        parser = VehicleFrameParser()
        self.assertEqual(parser.feed(self.raw[:13]), [])
        self.assertEqual(parser.feed(self.raw[13:31]), [])
        frames = parser.feed(self.raw[31:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].raw, self.raw)

    def test_handles_sticky_packets_and_prefix_noise(self):
        parser = VehicleFrameParser()
        frames = parser.feed(b"\x00noise" + self.raw + self.raw)
        self.assertEqual(len(frames), 2)
        self.assertEqual(parser.discarded_bytes, 6)

    def test_keeps_invalid_bcc_frame_as_raw_evidence(self):
        damaged = bytearray(self.raw)
        damaged[-1] ^= 0xFF
        frames = VehicleFrameParser().feed(damaged)
        self.assertEqual(len(frames), 1)
        self.assertFalse(frames[0].bcc_valid)
        self.assertEqual(frames[0].raw, bytes(damaged))


if __name__ == "__main__":
    unittest.main()
