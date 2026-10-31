"""Rekursive Aufloesung der Abhaengigkeiten und Zyklenerkennung."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence


class ResolverError(Exception):
    """Abhaengigkeit kann nicht aufgeloest werden."""


class Dependency(Protocol):
    name: str
    requires: Sequence[str]


def resolve_order(entries: Mapping[str, Dependency], name: str) -> list[Dependency]:
    """Liefert die Abhaengigkeiten zuerst, `name` zuletzt, ohne Duplikate.

    Wirft ResolverError bei unbekannten Abhaengigkeiten und Zyklen.
    """
    if name not in entries:
        raise ResolverError(f"Unbekannter Eintrag: {name!r}")

    order: list[Dependency] = []
    done: set[str] = set()
    stack: list[str] = []

    def visit(current: str) -> None:
        if current in done:
            return
        if current in stack:
            chain = " -> ".join([*stack[stack.index(current):], current])
            raise ResolverError(f"Zyklische Abhaengigkeit erkannt: {chain}")
        entry = entries.get(current)
        if entry is None:
            parent = stack[-1] if stack else name
            raise ResolverError(
                f"Fehlende Abhaengigkeit: {parent!r} benoetigt {current!r}, "
                "das nicht in der Konfiguration definiert ist"
            )
        stack.append(current)
        for dep in entry.requires:
            visit(dep)
        stack.pop()
        done.add(current)
        order.append(entry)

    visit(name)
    return order


def find_cycles(entries: Mapping[str, Dependency]) -> list[list[str]]:
    """Findet alle Zyklen, ohne bei der ersten Auffaelligkeit abzubrechen."""
    cycles: list[list[str]] = []
    visiting: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            cycles.append([*visiting[visiting.index(node):], node])
            return
        entry = entries.get(node)
        if entry is None:
            done.add(node)
            return
        visiting.append(node)
        for dep in entry.requires:
            visit(dep)
        visiting.pop()
        done.add(node)

    for name in entries:
        visit(name)
    return cycles
