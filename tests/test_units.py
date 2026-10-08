"""Independent numeric examples and input boundaries for exact M-L-T units."""
from dataclasses import FrozenInstanceError
from fractions import Fraction

import pytest

import resimind.units as units_module
from resimind.units import (
    AREA, DIMENSIONLESS, FORCE, LENGTH, MASS, PRESSURE, TIME,
    Dimension, Quantity, UnitRegistry,
)


@pytest.fixture
def units() -> UnitRegistry:
    return UnitRegistry()


def test_force_over_area_preserves_area_prefix_squared(units) -> None:
    stress = units.quantity(1000, "N") / units.quantity(100, "mm2")
    assert stress.dimension == PRESSURE
    assert stress.value == Fraction(10000000)
    assert units.convert(stress, "MPa") == Fraction(10)
    assert units.convert(stress, "kPa") == Fraction(10000)


def test_decimal_and_rational_values_remain_exact(units) -> None:
    a = units.quantity("0.1", "m")
    b = units.quantity("0.2", "m")
    assert units.convert(a + b, "m") == Fraction(3, 10)
    assert units.quantity("1/3", "kN").value == Fraction(1000, 3)
    tiny = "0.00000000000000000000000000000000000001"
    assert units.quantity(tiny, "m").value == Fraction(1, 10**38)
    large = "100000000000000000000000000000000000001/3"
    assert units.convert(units.quantity(large, "kN"), "kN") == Fraction(large)
    assert units.quantity("1.25e-3", "m").value == Fraction(1, 800)


@pytest.mark.parametrize("value", [
    "1e999999999", "1e-999999999", "1e+00001025", "1e-00001025",
    "1e" + "9" * 4094, "9" * 4097, "1/" + "9" * 4095,
])
def test_untrusted_numeric_limits_reject_before_fraction_construction(units, monkeypatch, value) -> None:
    def forbidden_fraction(*args, **kwargs):
        raise AssertionError("oversized input reached Fraction construction")

    monkeypatch.setattr(units_module, "Fraction", forbidden_fraction)
    with pytest.raises(ValueError, match="exceeds|scientific exponent"):
        units.parse(f"{value} Pa")


def test_numeric_text_boundaries_are_exact_and_trusted_objects_are_unbounded(units) -> None:
    assert units.quantity("1e1024", "Pa").value == Fraction(10**1024)
    assert units.quantity("1e-1024", "Pa").value == Fraction(1, 10**1024)
    assert units.quantity("1e+00001024", "Pa").value == Fraction(10**1024)
    assert units.quantity("1" + "0" * 4095, "Pa").value == Fraction(10**4095)
    # Trusted native numbers do not go through the untrusted text parser.
    huge = 10**5000
    assert units.quantity(huge, "Pa").value == Fraction(huge)
    assert units.quantity(Fraction(1, huge), "Pa").value == Fraction(1, huge)


def test_mixed_unit_addition_subtraction_and_unary_minus(units) -> None:
    a = units.quantity(1, "m")
    b = units.quantity(10, "mm")
    assert units.convert(a + b, "mm") == 1010
    assert units.convert(a - b, "mm") == 990
    assert units.convert(-b, "m") == Fraction(-1, 100)


def test_compound_dimensions_and_negative_powers(units) -> None:
    force = units.quantity(2, "kg") * units.quantity(3, "m") / units.quantity(2, "s") ** 2
    assert force.dimension == FORCE
    assert units.convert(force, "N") == Fraction(3, 2)
    inverse_area = units.quantity(2, "mm") ** -2
    assert inverse_area.value == Fraction(250000)
    assert inverse_area.dimension == Dimension(length=-2)
    ratio = units.quantity(1, "kN") / units.quantity(250, "N")
    assert ratio.dimension == DIMENSIONLESS
    assert units.convert(ratio, "1") == 4
    assert (units.quantity(12, "m") ** 0) == units.quantity(1, "1")


def test_dimensions_match_mechanical_definitions() -> None:
    assert FORCE == MASS * LENGTH / TIME ** 2
    assert AREA == LENGTH ** 2
    assert PRESSURE == Dimension(1, -1, -2)


def test_parse_requires_one_registered_unit(units) -> None:
    assert units.parse(" 1000 N ") == units.quantity(1, "kN")
    assert units.parse("1/3 MPa").value == Fraction(1000000, 3)
    assert units.parse("10\tmm") == units.quantity(1, "cm")


@pytest.mark.parametrize("text", ["1000", "1000N", "1000 N / 100 mm2", "", "1 N/mm2", "1 m^2", "1 m²"])
def test_parse_does_not_infer_or_evaluate_units(units, text) -> None:
    with pytest.raises(ValueError):
        units.parse(text)


@pytest.mark.parametrize("value", [True, False, 1.0, float("nan"), float("inf"), None, [], {}])
def test_quantity_rejects_inexact_or_ambiguous_value_types(units, value) -> None:
    with pytest.raises(TypeError):
        units.quantity(value, "m")


@pytest.mark.parametrize("value", ["nan", "NaN", "Inf", "Infinity", "1+2", "1_000", "0x10", " 1 ", "", "1/2/3", "__import__('os')"])
def test_numeric_strings_are_not_expressions(units, value) -> None:
    with pytest.raises(ValueError):
        units.quantity(value, "m")


@pytest.mark.parametrize("unit", ["", "metres", "mpa", "N/mm2", "mm^2", " m", None, True])
def test_unknown_and_missing_units_are_rejected(units, unit) -> None:
    with pytest.raises(ValueError):
        units.quantity(1, unit)


def test_mixed_dimensions_cannot_be_added_subtracted_or_converted(units) -> None:
    force = units.quantity(1, "N")
    length = units.quantity(1, "m")
    with pytest.raises(ValueError, match="dimension mismatch"):
        force + length
    with pytest.raises(ValueError, match="dimension mismatch"):
        force - length
    with pytest.raises(ValueError, match="dimension mismatch"):
        units.convert(force, "Pa")


@pytest.mark.parametrize("power", [True, False, 2.0, Fraction(1, 2), "2"])
def test_only_integer_powers_are_allowed(units, power) -> None:
    with pytest.raises(TypeError):
        units.quantity(2, "m") ** power


def test_zero_divisors_are_not_silently_coerced(units) -> None:
    with pytest.raises(ZeroDivisionError):
        units.quantity(1, "N") / units.quantity(0, "mm2")
    with pytest.raises(ZeroDivisionError):
        units.quantity(0, "m") ** -1
    with pytest.raises(ZeroDivisionError):
        units.quantity("1/0", "m")


def test_registry_extension_has_exact_positive_scale_and_no_redefinition(units) -> None:
    units.register("inch", LENGTH, Fraction(127, 5000))
    assert units.convert(units.quantity(1, "inch"), "mm") == Fraction(127, 5)
    assert "inch" in units.symbols
    with pytest.raises(ValueError, match="already registered"):
        units.register("m", LENGTH, Fraction(2))
    assert units.quantity(1, "m").value == 1
    with pytest.raises(ValueError, match="unknown unit"):
        UnitRegistry().quantity(1, "inch")
    assert UnitRegistry(defaults=False).symbols == ()


@pytest.mark.parametrize("scale", [Fraction(0), Fraction(-1)])
def test_registry_rejects_nonpositive_scales(units, scale) -> None:
    with pytest.raises(ValueError, match="positive"):
        units.register("bad", LENGTH, scale)


@pytest.mark.parametrize("scale", [True, 1, 1.0, "1", float("nan"), float("inf")])
def test_registration_requires_fraction_scale(units, scale) -> None:
    with pytest.raises(TypeError, match="Fraction"):
        units.register("bad", LENGTH, scale)


@pytest.mark.parametrize("symbol", ["", "m/s", "m²", " m", "2m", None])
def test_registry_rejects_nonatomic_or_ambiguous_symbols(units, symbol) -> None:
    with pytest.raises(ValueError, match="symbol"):
        units.register(symbol, LENGTH, Fraction(1))


@pytest.mark.parametrize("exponent", [True, 1.0, Fraction(1), "1"])
def test_dimension_exponents_are_strict_integers(exponent) -> None:
    with pytest.raises(TypeError, match="exponents"):
        Dimension(length=exponent)


def test_base_quantity_is_immutable_and_requires_explicit_exact_fields(units) -> None:
    quantity = units.quantity(Fraction(1, 3), "m")
    with pytest.raises(FrozenInstanceError):
        quantity.value = Fraction(7)
    with pytest.raises(FrozenInstanceError):
        quantity.dimension.length = 3
    for bad in (True, 1, 1.0, "1"):
        with pytest.raises(TypeError, match="Fraction"):
            Quantity(bad, LENGTH)
    with pytest.raises(TypeError, match="Dimension"):
        Quantity(Fraction(1), "length")
    with pytest.raises(TypeError):
        quantity + 1
    with pytest.raises(TypeError):
        quantity * 2
