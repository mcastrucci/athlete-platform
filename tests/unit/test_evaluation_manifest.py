"""Manifest hashing and ISO week parsing."""

from uuid import UUID

import pytest

from athlete_platform.application.evaluation import (
    ConfirmationRef,
    Manifest,
    build_manifest,
    manifest_hash,
    span_bounds,
    week_bounds,
)

A = UUID("00000000-0000-4000-8000-00000000000a")
B = UUID("00000000-0000-4000-8000-00000000000b")
C = UUID("00000000-0000-4000-8000-00000000000c")


def ref(source: UUID, version: int = 1) -> ConfirmationRef:
    return ConfirmationRef(source_id=source, interpretation_version=version)


def manifest(
    *refs: ConfirmationRef,
    week: str = "2030-W10",
    profile_version: int = 1,
    catalog_version: str = "catalog-v1",
    engine_version: str = "pull-budget-v1",
) -> Manifest:
    return build_manifest(
        week,
        profile_version,
        refs,
        catalog_version=catalog_version,
        engine_version=engine_version,
    )


def test_hash_is_a_stable_sha256_hex_digest() -> None:
    digest = manifest_hash(manifest(ref(A)))
    assert len(digest) == 64
    assert digest == manifest_hash(manifest(ref(A)))
    assert digest == "".join(c for c in digest if c in "0123456789abcdef")


def test_hash_ignores_the_order_of_the_confirmations() -> None:
    assert manifest_hash(manifest(ref(A), ref(B, 2), ref(C))) == manifest_hash(
        manifest(ref(C), ref(A), ref(B, 2))
    )
    unordered = Manifest(
        week="2030-W10",
        profile_version=1,
        catalog_version="catalog-v1",
        engine_version="pull-budget-v1",
        confirmations=[ref(B, 2), ref(A)],
    )
    assert manifest_hash(unordered) == manifest_hash(manifest(ref(A), ref(B, 2)))


def test_the_built_manifest_is_sorted_and_deduplicated() -> None:
    built = manifest(ref(B), ref(A), ref(B))
    assert [c.source_id for c in built.confirmations] == [A, B]


@pytest.mark.parametrize(
    "changed",
    [
        manifest(ref(A), week="2030-W11"),
        manifest(ref(A), profile_version=2),
        manifest(ref(A), catalog_version="catalog-v2"),
        manifest(ref(A), engine_version="pull-budget-v2"),
        manifest(ref(A), ref(B)),
        manifest(),
        manifest(ref(A, 2)),
        manifest(ref(B)),
    ],
)
def test_hash_changes_with_each_input(changed: Manifest) -> None:
    assert manifest_hash(changed) != manifest_hash(manifest(ref(A)))


def test_week_bounds_and_span() -> None:
    start, end = week_bounds("2030-W10")
    assert (start.isoformat(), end.isoformat()) == ("2030-03-04", "2030-03-10")
    first, last = span_bounds("2030-W10")
    assert (first.isoformat(), last.isoformat()) == ("2030-02-26", "2030-03-10")
    assert week_bounds("2026-W53")[0].isoformat() == "2026-12-28"


@pytest.mark.parametrize(
    "label",
    [
        "2030-W9",
        "2030-W00",
        "2030-W54",
        "2031-W53",
        "1999-W10",
        "2101-W01",
        "2030W10",
        "2030-w10",
        "2030-W10\n",
        " 2030-W10",
        "２０３０-W10",
        "2030-03-04",
        "",
    ],
)
def test_invalid_weeks_are_rejected(label: str) -> None:
    with pytest.raises(ValueError):
        week_bounds(label)
