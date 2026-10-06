from collections.abc import Callable, Iterator


class Registry[T]:
    """Name → implementation, filled by `@registry.register("name")`. Node handlers, actions, checks."""

    def __init__(self, kind: str) -> None:
        self.kind, self._items = kind, dict[str, T]()

    def register(self, name: str) -> Callable[[T], T]:
        def deco(fn: T) -> T:
            if name in self._items:
                raise ValueError(f"{self.kind} {name!r} registered twice")
            self._items[name] = fn
            return fn

        return deco

    def __getitem__(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"unknown {self.kind} {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._items))
