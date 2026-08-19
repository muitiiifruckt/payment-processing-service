import asyncio
import socket
from collections.abc import Awaitable, Callable, Iterable
from ipaddress import ip_address
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Awaitable[list[tuple[str, int]]]]

#: Имени нет и не будет — в отличие от «попробуйте позже»
PERMANENT_RESOLVER_ERRORS = frozenset(
    code
    for code in (getattr(socket, name, None) for name in ("EAI_NONAME", "EAI_NODATA"))
    if code is not None
)


class ForbiddenTargetError(Exception):
    """Адрес назначения webhook запрещён. Отказ постоянный: разрешение
    не появится от повтора."""


class TargetUnresolvedError(Exception):
    """Имя не удалось разрешить сейчас. Отказ временный."""


async def system_resolver(host: str, port: int) -> list[tuple[str, int]]:
    """Через loop: socket.getaddrinfo блокирующий, а на этом же цикле
    живут relay и соединение с брокером."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    return [(str(info[4][0]), port) for info in infos]


async def ensure_allowed(
    url: str,
    *,
    resolve: Resolver = system_resolver,
    allowed_hosts: Iterable[str] = (),
) -> None:
    """Запрос уходит изнутри сети по адресу, который назвал клиент. Без
    проверки сервис работает прокси во внутренний периметр — от метаданных
    облака до собственной базы."""
    try:
        split = urlsplit(url)
        host = split.hostname
        port = split.port
    except ValueError as error:
        # кривой адрес не станет годным от повтора: разбирать его заново
        # четыре прогона и класть в DLQ — впустую потраченный бюджет
        raise ForbiddenTargetError(f"адрес не разбирается: {error}") from error
    if not host:
        raise ForbiddenTargetError(f"в адресе {url} нет хоста")
    # список исключений снимает проверку с хоста целиком, а не только
    # запрет приватных адресов: любой путь на нём становится разрешённым
    if host.rstrip(".").lower() in {name.rstrip(".").lower() for name in allowed_hosts}:
        return

    port = port or (443 if split.scheme == "https" else 80)
    try:
        addresses = await resolve(host, port)
    except socket.gaierror as error:
        if error.errno in PERMANENT_RESOLVER_ERRORS:
            raise ForbiddenTargetError(f"хост {host} не существует: {error}") from error
        raise TargetUnresolvedError(f"хост {host} сейчас не разрешается: {error}") from error
    except OSError as error:
        raise TargetUnresolvedError(f"хост {host} сейчас не разрешается: {error}") from error

    for address, _ in addresses:
        if not _is_public(address):
            raise ForbiddenTargetError(f"адрес {address} для хоста {host} вне публичной сети")


def _is_public(address: str) -> bool:
    """is_global, а не перечисление диапазонов: оно накрывает и общее
    адресное пространство 100.64.0.0/10, и служебные подсети, которые
    поимённый список забывает."""
    try:
        return ip_address(address).is_global
    except ValueError:
        return False
