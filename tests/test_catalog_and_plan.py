import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from klinix.books.lfs import LfsProvider
from klinix.catalog import Catalog
from klinix.dependencies import DependencyGraph, Policy
from klinix.errors import KlinixError
from klinix.planner import plan
from klinix.recipes import Dependency, DependencyKind


def test_atomic_idempotent_import_and_pin_conflict(book_checkout, tmp_path: Path) -> None:
    root, revision = book_checkout
    book = LfsProvider().import_local(root, revision)
    path = tmp_path / "nested" / "catalog.sqlite3"
    catalog = Catalog(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(catalog.add, [book, book])) == [False, True]
    assert catalog.pins() == (book.pin,)
    assert catalog.load(revision) == book
    with pytest.raises(KlinixError, match="conflicts with existing revision"):
        catalog.add(replace(book, pin=replace(book.pin, version="99.2-systemd")))
    assert catalog.load(revision) == book
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM package").fetchone() == (2,)


def test_read_commands_leave_missing_catalog_untouched(tmp_path: Path) -> None:
    path = tmp_path / "no-catalog.sqlite3"
    with pytest.raises(KlinixError, match="Cannot access catalog"):
        Catalog(path).pins()
    assert not path.exists()


def test_plan_resolves_required_lfs_passes(book_checkout) -> None:
    book = LfsProvider().import_local(book_checkout[0])
    result = plan(book, ("core",))
    assert result["mode"] == "dry-run"
    assert result["selected"] == ["chapter08/core"]
    assert [recipe["id"] for recipe in result["recipes"]] == ["chapter05/core", "chapter08/core"]
    assert result["recipes"][0]["materials"][1]["kind"] == "patch"
    assert plan(book, ("chapter05/core",))["selected"] == ["chapter05/core"]
    assert [recipe["order"] for recipe in plan(book)["recipes"]] == [0, 1, 2]
    with pytest.raises(KlinixError, match="Unknown package"):
        plan(book, ("missing",))


def test_graph_policies_validate_missing_edges_and_detect_cycles(book_checkout) -> None:
    first, second, third = LfsProvider().import_local(book_checkout[0]).recipes
    graph = DependencyGraph((
        replace(first, dependencies=(Dependency(third.id, DependencyKind.OPTIONAL),)),
        replace(second, dependencies=(Dependency(first.id, DependencyKind.RECOMMENDED),)),
        third,
    ))
    assert graph.resolve([second.id]) == (graph.recipes[second.id],)
    assert graph.resolve([second.id], Policy(recommended=True)) == (graph.recipes[first.id], graph.recipes[second.id])
    assert graph.resolve([first.id], Policy(optional=True)) == (graph.recipes[second.id], third, graph.recipes[first.id])
    with pytest.raises(KlinixError, match=r"chapter05/core -> chapter08/systemd -> chapter08/core -> chapter05/core"):
        graph.resolve([first.id], Policy(recommended=True, optional=True))
    with pytest.raises(KlinixError, match="depends on unknown recipe"):
        DependencyGraph((second,))


def test_rejected_required_cycle_leaves_catalog_uncreated(book_checkout, tmp_path: Path) -> None:
    book = LfsProvider().import_local(book_checkout[0])
    first, *other = book.recipes
    invalid = replace(book, recipes=(replace(first, dependencies=(Dependency(other[-1].id),)), *other))
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(KlinixError, match="Dependency cycle"):
        Catalog(path).add(invalid)
    assert not path.exists()
