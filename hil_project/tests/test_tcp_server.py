import pytest

from app.config import TcpConfig
from app.events import EventBus
from app.protocol import PAYLOAD_LENGTH, READ_MESSAGE, WRITE_MESSAGE, encode_state
from app.state import StateStore
from app.tcp_server import TcpProtocolServer


@pytest.mark.asyncio
async def test_tcp_write_updates_state_and_returns_frame():
    store = StateStore(EventBus())
    server = TcpProtocolServer(TcpConfig(enabled=False), store)
    request = encode_state({"Throttle": 80, "Brake": 5}, message_type=WRITE_MESSAGE, message_id=3)

    response = await server.handle_frame(request)

    assert response[1] == WRITE_MESSAGE
    assert response[2] == 3
    assert response[11] == 80
    assert response[12] == 5
    assert store.snapshot()["Throttle"] == 80


@pytest.mark.asyncio
async def test_tcp_accepts_payload_length_header_and_echoes_it():
    store = StateStore(EventBus())
    server = TcpProtocolServer(TcpConfig(enabled=False), store)
    request = bytearray(encode_state({"Throttle": 33}, message_type=WRITE_MESSAGE, message_id=4))
    request[3] = PAYLOAD_LENGTH

    response = await server.handle_frame(bytes(request))

    assert response[1] == WRITE_MESSAGE
    assert response[2] == 4
    assert response[3] == PAYLOAD_LENGTH
    assert response[11] == 33


@pytest.mark.asyncio
async def test_tcp_accepts_payload_length_read_frame():
    store = StateStore(EventBus())
    server = TcpProtocolServer(TcpConfig(enabled=False), store)
    request = bytearray(encode_state({"Throttle": 0}, message_type=READ_MESSAGE, message_id=5))
    request[3] = PAYLOAD_LENGTH

    response = await server.handle_frame(bytes(request))

    assert response[1] == READ_MESSAGE
    assert response[2] == 5
    assert response[3] == PAYLOAD_LENGTH


@pytest.mark.asyncio
async def test_tcp_accepts_one_byte_read_placeholder_frame():
    store = StateStore(EventBus())
    server = TcpProtocolServer(TcpConfig(enabled=False), store)

    response = await server.handle_frame(bytes([0x0C, READ_MESSAGE, 0xE1, 0x05, 0x00]))

    assert response[0] == 0x0C
    assert response[1] == READ_MESSAGE
    assert response[2] == 0xE1
    assert response[3] == 14
    assert len(response) == 14


def test_tcp_frame_formatter_includes_raw_hex_and_header_fields():
    store = StateStore(EventBus())
    server = TcpProtocolServer(TcpConfig(enabled=False), store)

    summary = server._format_frame(bytes([0x0C, READ_MESSAGE, 0x12, PAYLOAD_LENGTH, 0xAA]))

    assert "hex=0C 00 12 0A AA" in summary
    assert "version=0x0C" in summary
    assert "type=0x00" in summary
    assert "id=0x12" in summary
    assert "length=10" in summary
    assert "payload_len=1" in summary
