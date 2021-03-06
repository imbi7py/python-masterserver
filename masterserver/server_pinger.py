import asyncio
from ipaddress import IPv4Address
from typing import Union, Text, Tuple

from . import get_logger


class PingError(Exception):
    def __init__(self, message: str, wrapped_exception: Exception = None):
        self.wrapped_exception = wrapped_exception
        self.__cause__ = wrapped_exception


class PingProtocol(asyncio.DatagramProtocol):
    # dependency injection for the win
    def __init__(self, connection_made: asyncio.Future, reply_received: asyncio.Future):
        self._transport: Union[asyncio.DatagramTransport, None] = None

        self._connection_made: asyncio.Future = connection_made
        self._reply_received: asyncio.Future = reply_received

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport
        self._connection_made.set_result(True)

    def datagram_received(self, data: Union[bytes, Text], addr: Tuple[str, int]) -> None:
        self._reply_received.set_result(data)

        # make sure socket is closed
        self._transport.close()

    def error_received(self, exc: Exception) -> None:
        self._reply_received.set_exception(PingError("error received", exc))

        # make sure socket is closed
        self._transport.close()


class ServerPinger:
    _logger = get_logger("server_pinger")

    def __init__(self, host: Union[IPv4Address, str], port: int):
        self._host = host
        self._port = port

    async def ping(self):
        loop = asyncio.get_event_loop()

        reply_received = loop.create_future()
        connection_made = loop.create_future()

        try:
            host = self._host.exploded
        except AttributeError:
            host = self._host

        transport: asyncio.DatagramTransport
        protocol: PingProtocol
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: PingProtocol(connection_made, reply_received),
            remote_addr=(host, self._port)
        )

        try:
            # wait for connection
            await connection_made
            self._logger.debug("connection made")

            # default amount of valid pings is 5, see master.cpp
            for i in range(5):
                self._logger.debug("sending request %d", i)

                # ENet style packet containing a single \x01, probably stolen from some server browser
                # sending a single \x01 also seems to work, though
                transport.sendto(b"\x81\xec\x04\x01\x00")

                # need to check before wait_for to avoid deadlocks
                if reply_received.done():
                    self._logger.debug("done")
                    break

                try:
                    # need to shield future, otherwise it will be cancelled by wait_for
                    await asyncio.wait_for(asyncio.shield(reply_received), timeout=1)

                except asyncio.TimeoutError:
                    self._logger.debug("timeout")
                    continue

                else:
                    break

            if not reply_received.done():
                reply_received.cancel()
                raise TimeoutError()

            if reply_received.exception():
                raise reply_received.exception()

            return reply_received.result()

        finally:
            # make sure we don't leak sockets
            transport.close()
