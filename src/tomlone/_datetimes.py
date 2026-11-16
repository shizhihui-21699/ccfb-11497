"""TOML 1.0 date/time value types.

TOML has four distinct date-time types. Python's stdlib models only three
(``datetime``/``date``/``time``) and its microsecond field is capped at six
digits, while TOML permits any number of fractional digits. These lightweight
types keep the original precision and expose the exact type names used by the
official ``toml-test`` encoding.

All instances are immutable; equality is structural so that two values parsed
from different spelling (e.g. ``Z`` vs ``+00:00``) are distinguishable at the
raw-text level but comparable component-wise.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offset:
    """A UTC offset in minutes, east of Greenwich (negative means west)."""

    minutes: int

    @classmethod
    def from_parts(cls, sign: int, hour: int, minute: int) -> "Offset":
        return cls(sign * (hour * 60 + minute))

    def canonical(self) -> str:
        if self.minutes == 0:
            return "Z"
        sign = "+" if self.minutes > 0 else "-"
        m = abs(self.minutes)
        return f"{sign}{m // 60:02d}:{m % 60:02d}"


@dataclass(frozen=True)
class LocalDate:
    year: int
    month: int
    day: int
    type_name: str = "date-local"

    def canonical(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


@dataclass(frozen=True)
class LocalTime:
    hour: int
    minute: int
    second: int
    # Fractional seconds exactly as written, including the leading dot,
    # e.g. ".5" or ".123456789". None when no fraction was present.
    fraction: str | None
    type_name: str = "time-local"

    def canonical(self) -> str:
        base = f"{self.hour:02d}:{self.minute:02d}:{self.second:02d}"
        return base + self.fraction if self.fraction is not None else base

    def microseconds(self) -> int:
        if self.fraction is None:
            return 0
        digits = self.fraction[1:]
        return int(digits.ljust(6, "0")[:6])


@dataclass(frozen=True)
class LocalDateTime:
    date: LocalDate
    time: LocalTime
    type_name: str = "datetime-local"

    def canonical(self) -> str:
        return f"{self.date.canonical()}T{self.time.canonical()}"


@dataclass(frozen=True)
class OffsetDateTime:
    date: LocalDate
    time: LocalTime
    offset: Offset
    type_name: str = "datetime"

    def canonical(self) -> str:
        return (
            f"{self.date.canonical()}T{self.time.canonical()}"
            f"{self.offset.canonical()}"
        )
