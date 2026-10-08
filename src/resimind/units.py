"""Exact quantities for an explicit multiplicative mass/length/time subset.

This is not a complete SI implementation: it has no offset units, temperatures,
angles, uncertainties or automatic unit inference. Values normalize to kg, m
and s; all arithmetic uses ``Fraction``. Unit symbols are registered whole
tokens (``mm2`` is supported; expressions such as ``N/mm2`` are not parsed).
Build formulas with Quantity operations, never by evaluating input text.

Untrusted numeric strings are limited to 4096 characters and scientific
exponents from -1024 through 1024 before Fraction construction. Native int
and Fraction inputs are trusted application objects and have no size limit;
applications must bound their own arithmetic and integer powers as needed.

Examples::

    units = UnitRegistry()
    stress = units.quantity(1000, "N") / units.quantity(100, "mm2")
    assert units.convert(stress, "MPa") == Fraction(10)

Unit definitions use the SI newton (kg m s^-2), pascal (N m^-2) and
decimal prefixes: https://www.bipm.org/en/publications/si-brochure
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import re
from typing import TypeAlias


ExactValue: TypeAlias = int | Fraction | str
MAX_NUMERIC_TEXT_LENGTH = 4096
MAX_SCIENTIFIC_EXPONENT = 1024
_NUMBER = re.compile(r"[+-]?(?:[0-9]+/[0-9]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE](?P<exponent>[+-]?[0-9]+))?)\Z")
_SYMBOL = re.compile(r"(?:1|[A-Za-z][A-Za-z0-9]*)\Z")


def _fraction(value: ExactValue) -> Fraction:
    """Accept explicit exact numbers, rejecting booleans and binary floats."""
    if type(value) is Fraction:
        return value
    if type(value) is int:
        return Fraction(value)
    if type(value) is not str:
        raise TypeError("value must be an int, Fraction or exact numeric string; bool and float are not allowed")
    if len(value) > MAX_NUMERIC_TEXT_LENGTH:
        raise ValueError(f"numeric string exceeds {MAX_NUMERIC_TEXT_LENGTH} characters")
    match = _NUMBER.fullmatch(value)
    if not match:
        raise ValueError("value must be an integer, rational, decimal or scientific numeric string")
    if match.group("exponent") is not None:
        # Compare digit strings before numeric conversion, including exponents
        # with thousands of digits or leading zeroes supplied by a proposer.
        exponent = match.group("exponent").lstrip("+-").lstrip("0") or "0"
        limit = str(MAX_SCIENTIFIC_EXPONENT)
        if len(exponent) > len(limit) or (len(exponent) == len(limit) and exponent > limit):
            raise ValueError(f"scientific exponent must be between -{limit} and {limit}")
    return Fraction(value)


@dataclass(frozen=True, slots=True)
class Dimension:
    """Integer powers of the mass, length and time base dimensions."""

    mass: int = 0
    length: int = 0
    time: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (self.mass, self.length, self.time)):
            raise TypeError("dimension exponents must be integers, not bool or float")

    def __mul__(self, other: Dimension) -> Dimension:
        if type(other) is not Dimension:
            return NotImplemented
        return Dimension(self.mass + other.mass, self.length + other.length, self.time + other.time)

    def __truediv__(self, other: Dimension) -> Dimension:
        if type(other) is not Dimension:
            return NotImplemented
        return Dimension(self.mass - other.mass, self.length - other.length, self.time - other.time)

    def __pow__(self, exponent: int) -> Dimension:
        if type(exponent) is not int:
            raise TypeError("quantity and dimension powers must be integers, not bool or float")
        return Dimension(self.mass * exponent, self.length * exponent, self.time * exponent)


DIMENSIONLESS = Dimension()
MASS = Dimension(mass=1)
LENGTH = Dimension(length=1)
TIME = Dimension(time=1)
AREA = LENGTH ** 2
FORCE = Dimension(mass=1, length=1, time=-2)
PRESSURE = FORCE / AREA


@dataclass(frozen=True, slots=True)
class Quantity:
    """Exact base-unit value and dimension, independent of display units.

    Direct construction requires a Fraction. Use UnitRegistry.quantity to
    normalize an explicitly supplied number and unit into this representation.
    Quantities must be combined explicitly; bare numbers are not coerced into
    quantities. Even a dimensionless number must name the registered unit "1".
    """

    value: Fraction
    dimension: Dimension

    def __post_init__(self) -> None:
        if type(self.value) is not Fraction:
            raise TypeError("normalized quantity value must be a Fraction")
        if type(self.dimension) is not Dimension:
            raise TypeError("quantity dimension must be a Dimension")

    def _same_dimension(self, other: Quantity) -> None:
        if self.dimension != other.dimension:
            raise ValueError(f"dimension mismatch: {self.dimension} versus {other.dimension}")

    def __add__(self, other: Quantity) -> Quantity:
        if type(other) is not Quantity:
            return NotImplemented
        self._same_dimension(other)
        return Quantity(self.value + other.value, self.dimension)

    def __sub__(self, other: Quantity) -> Quantity:
        if type(other) is not Quantity:
            return NotImplemented
        self._same_dimension(other)
        return Quantity(self.value - other.value, self.dimension)

    def __mul__(self, other: Quantity) -> Quantity:
        if type(other) is not Quantity:
            return NotImplemented
        return Quantity(self.value * other.value, self.dimension * other.dimension)

    def __truediv__(self, other: Quantity) -> Quantity:
        if type(other) is not Quantity:
            return NotImplemented
        return Quantity(self.value / other.value, self.dimension / other.dimension)

    def __pow__(self, exponent: int) -> Quantity:
        dimension = self.dimension ** exponent
        return Quantity(self.value ** exponent, dimension)

    def __neg__(self) -> Quantity:
        return Quantity(-self.value, self.dimension)


@dataclass(frozen=True, slots=True)
class _Unit:
    dimension: Dimension
    scale: Fraction


class UnitRegistry:
    """Explicit unit definitions, each scaled positively from kg, m and s.

    Defaults are ``1 kg g m cm mm s m2 cm2 mm2 N kN Pa kPa MPa GPa``.
    Each registry owns its definitions. Existing symbols cannot be redefined,
    preventing an extension from silently changing a prior interpretation.
    Use ``UnitRegistry(defaults=False)`` for an initially empty registry.
    """

    def __init__(self, *, defaults: bool = True) -> None:
        if type(defaults) is not bool:
            raise TypeError("defaults must be a bool")
        self._units: dict[str, _Unit] = {}
        if defaults:
            for symbol, dimension, scale in (
                ("1", DIMENSIONLESS, Fraction(1)),
                ("kg", MASS, Fraction(1)),
                ("g", MASS, Fraction(1, 1000)),
                ("m", LENGTH, Fraction(1)),
                ("cm", LENGTH, Fraction(1, 100)),
                ("mm", LENGTH, Fraction(1, 1000)),
                ("s", TIME, Fraction(1)),
                ("m2", AREA, Fraction(1)),
                ("cm2", AREA, Fraction(1, 10000)),
                ("mm2", AREA, Fraction(1, 1000000)),
                ("N", FORCE, Fraction(1)),
                ("kN", FORCE, Fraction(1000)),
                ("Pa", PRESSURE, Fraction(1)),
                ("kPa", PRESSURE, Fraction(1000)),
                ("MPa", PRESSURE, Fraction(1000000)),
                ("GPa", PRESSURE, Fraction(1000000000)),
            ):
                self.register(symbol, dimension, scale)

    @property
    def symbols(self) -> tuple[str, ...]:
        """A detached, sorted inventory of exact case-sensitive symbols."""
        return tuple(sorted(self._units))

    def register(self, symbol: str, dimension: Dimension, scale: Fraction) -> None:
        """Register one ASCII symbol; scale must be a strictly positive Fraction."""
        if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
            raise ValueError("unit symbol must be ASCII letters followed by letters/digits, or '1'")
        if type(dimension) is not Dimension:
            raise TypeError("unit dimension must be a Dimension")
        if type(scale) is not Fraction:
            raise TypeError("unit scale must be a Fraction")
        if scale <= 0:
            raise ValueError("unit scale must be positive")
        if symbol in self._units:
            raise ValueError(f"unit already registered: {symbol!r}")
        self._units[symbol] = _Unit(dimension, scale)

    def _lookup(self, symbol: str) -> _Unit:
        if type(symbol) is not str or not symbol:
            raise ValueError("an explicit registered unit symbol is required")
        try:
            return self._units[symbol]
        except KeyError:
            raise ValueError(f"unknown unit {symbol!r}; register it explicitly before use") from None

    def quantity(self, value: ExactValue, unit: str) -> Quantity:
        """Create an exact quantity from an explicit number and registered unit."""
        definition = self._lookup(unit)
        return Quantity(_fraction(value) * definition.scale, definition.dimension)

    def parse(self, text: str) -> Quantity:
        """Parse exactly ``<number> <registered-symbol>``; never parse a formula."""
        if type(text) is not str:
            raise TypeError("quantity text must be a string")
        parts = text.split()
        if len(parts) != 2:
            raise ValueError("quantity text must contain one number and one explicit unit, separated by whitespace")
        return self.quantity(parts[0], parts[1])

    def convert(self, quantity: Quantity, unit: str) -> Fraction:
        """Return the exact numeric value in a dimensionally compatible unit."""
        if type(quantity) is not Quantity:
            raise TypeError("conversion requires a Quantity")
        definition = self._lookup(unit)
        if quantity.dimension != definition.dimension:
            raise ValueError(f"cannot convert {quantity.dimension} to unit {unit!r}: dimension mismatch")
        return quantity.value / definition.scale
