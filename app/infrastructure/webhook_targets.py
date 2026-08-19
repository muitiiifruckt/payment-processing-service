import socket
from collections.abc import Callable, Iterable
from ipaddress import ip_address
from urllib.parse import urlsplit

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
    """Запрос уходит изнутри сети по адресу, который назвал клиент. Без
    проверки сервис работает прокси во внутренний периметр — от метаданных
    облака до собственной базы."""
    host = urlsplit(url).hostname
    if not host:
        raise ForbiddenTargetError(f"в адресе {url} нет хоста")
    if host in set(allowed_hosts):
        return

    port = urlsplit(url).port or (443 if url.startswith("https://") else 80)
    try:
        addresses = resolve(host, port)
    except OSError as error:
        # неразрешимое имя незачем и пытаться: заодно не даём отличить
        # существующий внутренний хост от несуществующего по времени ответа
        raise ForbiddenTargetError(f"хост {host} не разрешается: {error}") from error

    for address, _ in addresses:
        if not _is_public(address):
            raise ForbiddenTargetError(f"адрес {address} для хоста {host} вне публичной сети")


def _is_public(address: str) -> bool:
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )
