import pytest

from app.infrastructure.webhook_targets import ForbiddenTargetError, ensure_allowed

PUBLIC = [("93.184.216.34", 443)]
PRIVATE = [("10.0.0.7", 80)]


def resolver(addresses: list[tuple[str, int]]):  # type: ignore[no-untyped-def]
    def resolve(host: str, port: int) -> list[tuple[str, int]]:
        return addresses

    return resolve


def test_a_public_address_is_allowed() -> None:
    ensure_allowed("https://receiver.example/hook", resolve=resolver(PUBLIC))


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
def test_an_address_inside_the_perimeter_is_refused(url: str) -> None:
    """Адрес задаёт клиент, а запрос уходит изнутри сети: без проверки
    сервис работает прокси во внутренний периметр."""
    with pytest.raises(ForbiddenTargetError):
        ensure_allowed(url, resolve=resolver(PRIVATE))


def test_a_name_resolving_into_the_perimeter_is_refused() -> None:
    """Публичное имя, указывающее на приватный адрес, — тот же случай."""
    with pytest.raises(ForbiddenTargetError):
        ensure_allowed("https://internal.example/hook", resolve=resolver(PRIVATE))


def test_an_explicitly_allowed_host_passes() -> None:
    """Демонстрационный приёмник живёт внутри сети compose, и без списка
    исключений сквозной прогон невозможен."""
    ensure_allowed(
        "http://api:8000/__sink__/webhook", resolve=resolver(PRIVATE), allowed_hosts={"api"}
    )


def test_an_unresolvable_host_is_refused() -> None:
    def failing(host: str, port: int) -> list[tuple[str, int]]:
        raise OSError("имя не разрешается")

    with pytest.raises(ForbiddenTargetError):
        ensure_allowed("https://nowhere.invalid/hook", resolve=failing)
