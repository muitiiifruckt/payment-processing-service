import socket

import pytest

from app.infrastructure.webhook_targets import (
    ForbiddenTargetError,
    TargetUnresolvedError,
    ensure_allowed,
)

PUBLIC = [("93.184.216.34", 443)]
PRIVATE = [("10.0.0.7", 80)]


def resolver(addresses: list[tuple[str, int]]):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[tuple[str, int]]:
        return addresses

    return resolve


def failing_resolver(error: OSError):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[tuple[str, int]]:
        raise error

    return resolve


async def test_a_public_address_is_allowed() -> None:
    await ensure_allowed("https://receiver.example/hook", resolve=resolver(PUBLIC))


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # метаданные облака
        "http://127.0.0.1:8000/hook",
        "http://localhost:5432/",
        "http://10.0.0.7/hook",
        "http://192.168.1.1/hook",
        "http://[::1]/hook",
    ],
)
async def test_an_address_inside_the_perimeter_is_refused(url: str) -> None:
    """Адрес задаёт клиент, а запрос уходит изнутри сети: без проверки
    сервис работает прокси во внутренний периметр."""
    with pytest.raises(ForbiddenTargetError):
        await ensure_allowed(url, resolve=resolver(PRIVATE))


async def test_a_name_resolving_into_the_perimeter_is_refused() -> None:
    """Публичное имя, указывающее на приватный адрес, — тот же случай."""
    with pytest.raises(ForbiddenTargetError):
        await ensure_allowed("https://internal.example/hook", resolve=resolver(PRIVATE))


async def test_an_explicitly_allowed_host_passes() -> None:
    """Демонстрационный приёмник живёт внутри сети compose, и без списка
    исключений сквозной прогон невозможен."""
    await ensure_allowed(
        "http://api:8000/__sink__/webhook", resolve=resolver(PRIVATE), allowed_hosts={"api"}
    )


async def test_the_allow_list_is_matched_regardless_of_case() -> None:
    """hostname из URL всегда в нижнем регистре, а список пишет человек."""
    await ensure_allowed("http://API:8000/hook", resolve=resolver(PRIVATE), allowed_hosts={"Api"})


async def test_a_nonexistent_name_is_refused_for_good() -> None:
    """Несуществующего имени не появится от повтора."""
    with pytest.raises(ForbiddenTargetError):
        await ensure_allowed(
            "https://nowhere.invalid/hook",
            resolve=failing_resolver(socket.gaierror(socket.EAI_NONAME, "имя не найдено")),
        )


async def test_a_temporary_resolver_failure_is_not_permanent() -> None:
    """Резолвер моргнул — это не повод потерять webhook по уже
    обработанному платежу навсегда."""
    with pytest.raises(TargetUnresolvedError):
        await ensure_allowed(
            "https://receiver.example/hook",
            resolve=failing_resolver(socket.gaierror(socket.EAI_AGAIN, "попробуйте позже")),
        )


async def test_the_shared_address_space_is_refused() -> None:
    """100.64.0.0/10 не is_private, но это внутренние сети узлов и CGNAT."""
    with pytest.raises(ForbiddenTargetError):
        await ensure_allowed("https://node.example/hook", resolve=resolver([("100.64.0.1", 443)]))
