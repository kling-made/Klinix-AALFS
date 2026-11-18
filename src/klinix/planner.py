"""Select recipes and assemble a dry-run plan without system operations."""

from dataclasses import asdict

from klinix.dependencies import DependencyGraph, Policy
from klinix.errors import KlinixError
from klinix.recipes import Book


def plan(book: Book, selection: tuple[str, ...] = (), policy: Policy = Policy()) -> dict:
    graph = DependencyGraph(book.recipes)
    packages = {
        recipe.package.name: recipe.id for recipe in book.recipes if recipe.package is not None
    }
    selected = []
    for name in selection:
        if name in graph.recipes:
            selected.append(name)
        elif name.casefold() in packages:
            selected.append(packages[name.casefold()])
        else:
            raise KlinixError(f"Unknown package or recipe {name!r}")
    roots = tuple(dict.fromkeys(selected)) if selection else tuple(graph.recipes)
    resolved = graph.resolve(roots, policy)
    artifacts = {artifact.url: artifact for artifact in book.artifacts}
    return {
        "mode": "dry-run",
        "book": asdict(book.pin),
        "instruction_license": book.instruction_license,
        "policy": asdict(policy),
        "selected": list(roots),
        "recipes": [
            {
                **asdict(recipe),
                "materials": [asdict(artifacts[url]) for url in recipe.materials],
            }
            for recipe in resolved
        ],
    }
