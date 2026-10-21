from __future__ import annotations

import asyncio
import contextlib
import logging

from app.config import TcpConfig
from app.protocol import (
    FRAME_LENGTH,
    HEADER_LENGTH,
    PAYLOAD_LENGTH,
    READ_MESSAGE,
    WRITE_MESSAGE,
    ProtocolError,
    decode_payload,
    encode_state,
    parse_frame,
)
from app.state import StateStore

LOGGER = logging.getLogger(__name__)


class TcpProtocolServer:
    def __init__(self, config: TcpConfig, state_store: StateStore) -> None:
        self._config = config
        self._state_store = state_store
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if not self._config.enabled:
            return
        self._server = await asyncio.start_server(self._handle_client, self._config.host, self._config.port)
        sockets = ", ".join(str(socket.getsockname()) for socket in self._server.sockets or [])
        LOGGER.info("TCP protocol server listening on %s", sockets)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        request = b""
        try:
            while not reader.at_eof():
                header = await reader.readexactly(4)
                request = header
                payload_length = self._payload_length_from_header(header)
                payload = await reader.readexactly(payload_length)
                request = header + payload
                self._log_frame("RX", peer, request)
                response = await self.handle_frame(request)
                self._log_frame("TX", peer, response)
                writer.write(response)
                await writer.drain()
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                request += exc.partial
                LOGGER.warning("TCP incomplete request from %s: %s", peer, self._format_frame(request))
            else:
                LOGGER.debug("TCP client disconnected: %s", peer)
        except ProtocolError as exc:
            LOGGER.warning("TCP protocol error from %s: %s; request=%s", peer, exc, self._format_frame(request))
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _payload_length_from_header(self, header: bytes) -> int:
        if len(header) != HEADER_LENGTH:
            raise ProtocolError(f"Invalid header length: {len(header)}.")

        message_type = header[1]
        length = header[3]
        if message_type == READ_MESSAGE:
            if length in {0, HEADER_LENGTH}:
                return 0
            if length in {PAYLOAD_LENGTH, FRAME_LENGTH}:
                return PAYLOAD_LENGTH
        elif message_type == WRITE_MESSAGE:
            if length in {PAYLOAD_LENGTH, FRAME_LENGTH}:
                return PAYLOAD_LENGTH

        if length >= HEADER_LENGTH:
            return length - HEADER_LENGTH
        raise ProtocolError(f"Invalid frame length: {length}.")

    def _log_frame(self, direction: str, peer: object, data: bytes) -> None:
        if self._config.log_frames:
            LOGGER.info("TCP %s %s: %s", direction, peer, self._format_frame(data))

    def _format_frame(self, data: bytes) -> str:
        summary = f"len={len(data)} hex={data.hex(' ').upper()}"
        if len(data) >= HEADER_LENGTH:
            version, message_type, message_id, length = data[:HEADER_LENGTH]
            summary += (
                f" version=0x{version:02X}"
                f" type=0x{message_type:02X}"
                f" id=0x{message_id:02X}"
                f" length={length}"
                f" payload_len={max(len(data) - HEADER_LENGTH, 0)}"
            )
        return summary

    async def handle_frame(self, data: bytes) -> bytes:
        frame = parse_frame(data)
        if frame.message_type == WRITE_MESSAGE:
            values = decode_payload(data, self._config.steering_endian)
            await self._state_store.apply_updates("tcp", values)

        message_type = READ_MESSAGE if frame.message_type == READ_MESSAGE else WRITE_MESSAGE
        response = encode_state(
            self._state_store.snapshot(),
            version=self._config.version,
            message_id=frame.message_id,
            message_type=message_type,
            steering_endian=self._config.steering_endian,
        )
        if frame.length == PAYLOAD_LENGTH:
            response = bytearray(response)
            response[3] = PAYLOAD_LENGTH
            return bytes(response)
        return response
