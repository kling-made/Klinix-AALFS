"""Stable dependency resolution for a single book snapshot."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from klinix.errors import KlinixError
from klinix.recipes import DependencyKind, Recipe


@dataclass(frozen=True)
class Policy:
    recommended: bool = False
    optional: bool = False

    def includes(self, kind: DependencyKind) -> bool:
        return {
            DependencyKind.REQUIRED: True,
            DependencyKind.RECOMMENDED: self.recommended,
            DependencyKind.OPTIONAL: self.optional,
        }[kind]


class DependencyGraph:
    def __init__(self, recipes: Iterable[Recipe]) -> None:
        self.recipes: dict[str, Recipe] = {}
        for recipe in recipes:
            if recipe.id in self.recipes:
                raise KlinixError(f"Duplicate recipe ID {recipe.id}")
            self.recipes[recipe.id] = recipe
        for recipe in self.recipes.values():
            for dependency in recipe.dependencies:
                if dependency.recipe_id not in self.recipes:
                    raise KlinixError(f"{recipe.id} depends on unknown recipe {dependency.recipe_id}")

    def resolve(self, selected: Iterable[str], policy: Policy = Policy()) -> tuple[Recipe, ...]:
        roots = set(selected)
        missing = roots - self.recipes.keys()
        if missing:
            raise KlinixError(f"Unknown recipes: {', '.join(sorted(missing))}")

        def order(recipe_id: str) -> tuple[int, str]:
            return self.recipes[recipe_id].order, recipe_id

        def parents(recipe_id: str) -> Iterator[str]:
            return iter(sorted(
                (edge.recipe_id for edge in self.recipes[recipe_id].dependencies if policy.includes(edge.kind)),
                key=order,
            ))

        # Postorder traversal with an explicit stack keeps deep dependency
        # chains independent of Python's recursion limit.
        finished: dict[str, Recipe] = {}
        active: dict[str, int] = {}
        for root in sorted(roots, key=order):
            if root in finished:
                continue
            active[root] = 0
            stack = [(root, parents(root))]
            while stack:
                current, pending = stack[-1]
                parent = next(pending, None)
                if parent is None:
                    finished[current] = self.recipes[current]
                    del active[current]
                    stack.pop()
                    continue
                if parent in active:
                    cycle = [name for name, _ in stack[active[parent]:]] + [parent]
                    raise KlinixError(f"Dependency cycle: {' -> '.join(cycle)}")
                if parent not in finished:
                    active[parent] = len(stack)
                    stack.append((parent, parents(parent)))

        return tuple(finished.values())
