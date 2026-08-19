import socket
from collections.abc import Callable, Iterable

Resolver = Callable[[str, int], list[tuple[str, int]]]


class ForbiddenTargetError(Exception):
    """Адрес назначения webhook запрещён. Отказ постоянный: разрешение
    не появится от повтора."""


def system_resolver(host: str, port: int) -> list[tuple[str, int]]:
    return [(str(info[4][0]), port) for info in socket.getaddrinfo(host, port)]


def ensure_allowed(
    url: str,
    *,
    resolve: Resolver = system_resolver,
    allowed_hosts: Iterable[str] = (),
) -> None:
    raise ForbiddenTargetError("не реализовано")
