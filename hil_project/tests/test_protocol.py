import pytest

from app.protocol import FRAME_LENGTH, ProtocolError, decode_payload, encode_state, parse_frame


def test_encode_decode_little_endian_steering():
    signals = {
        "S2-N": 1,
        "S2-D": 1,
        "Steering": -450,
        "Throttle": 64,
        "Brake": 10,
    }

    frame = encode_state(signals, message_id=7, steering_endian="little")

    assert len(frame) == FRAME_LENGTH
    assert frame[0] == 0x0C
    assert frame[2] == 7
    assert frame[3] == FRAME_LENGTH
    assert frame[4] & 0b00000101 == 0b00000101
    assert frame[9:11] == (-(450)).to_bytes(2, "little", signed=True)
    assert decode_payload(frame, "little")["Steering"] == -450


def test_encode_decode_big_endian_steering():
    frame = encode_state({"Steering": 450}, steering_endian="big")

    assert frame[9:11] == (450).to_bytes(2, "big", signed=True)
    assert decode_payload(frame, "big")["Steering"] == 450


def test_parse_frame_rejects_bad_length():
    with pytest.raises(ProtocolError):
        parse_frame(bytes([0x0C, 0x00, 0x01, 0x06, 0x00]))


def test_parse_frame_accepts_payload_length_header():
    frame = bytearray(encode_state({"Throttle": 42}, message_id=2))
    frame[3] = FRAME_LENGTH - 4

    parsed = parse_frame(bytes(frame))

    assert parsed.length == FRAME_LENGTH - 4
    assert parsed.payload == bytes(frame[4:])


def test_parse_frame_accepts_one_byte_read_placeholder():
    parsed = parse_frame(bytes([0x0C, 0x00, 0xE1, 0x05, 0x00]))

    assert parsed.message_type == 0x00
    assert parsed.message_id == 0xE1
    assert parsed.length == 5
    assert parsed.payload == b"\x00"


def test_parse_frame_rejects_bad_type():
    with pytest.raises(ProtocolError):
        parse_frame(bytes([0x0C, 0x7F, 0x01, 0x04]))
