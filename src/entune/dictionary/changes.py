"""Changing the dictionary: pinning, sharing, and proposals reviewed before they apply.

Pinning shares and protects knowledge; it never chooses a meaning over a competitor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from entune.dictionary.document import _list, _object, parse_groups, validate
from entune.dictionary.entries import Dictionary, Group, Groups, _select, key, merge


def pin(dictionary: Dictionary, model: str, group_id: str, meaning_id: str) -> Dictionary:
    """Share a meaning's associations everywhere, leaving competitors model-local."""
    group = next((g for g in dictionary.learned_for(model) if g.id == group_id), None)
    if group is None:
        raise ValueError("That learned group is no longer present")
    meaning = next((m for m in group.meanings if m.id == meaning_id), None)
    if meaning is None:
        raise ValueError("That learned meaning is no longer present")
    return share(dictionary, {meaning_id})


def share(dictionary: Dictionary, ids: set[str]) -> Dictionary:
    """Normalize all pinned variants, including extensions in other groups/models."""
    ids = ids | {m.id for g in dictionary.pinned for m in g.meanings}
    return validate(
        Dictionary(
            merge(
                *(
                    _select(gs, ids, included=True)
                    for gs in (dictionary.pinned, *dictionary.learned.values())
                )
            ),
            {
                model: local
                for model, gs in dictionary.learned.items()
                if (local := _select(gs, ids, included=False))
            },
        )
    )


def protect_pinned(pinned: Groups, working: Groups) -> None:
    """Agent proposals may refine definitions, never erase protected knowledge."""
    meanings = {m.id: m for g in working for m in g.meanings}
    links = {
        (key(f.text), a.meaning_id)
        for g in working
        for f in g.recognized_forms
        for a in f.associations
    }
    for group in pinned:
        for old in group.meanings:
            new = meanings.get(old.id)
            if new is None or (old.spelling, old.casing) != (new.spelling, new.casing):
                raise ValueError(
                    "The generator cannot delete or change a pinned meaning's spelling"
                )
        for form in group.recognized_forms:
            if any((key(form.text), a.meaning_id) not in links for a in form.associations):
                raise ValueError("The generator cannot remove an existing pinned variant")


def refined(current: Dictionary, working: Groups, model: str) -> Dictionary:
    """Partition a reviewed working dictionary into shared and model-local knowledge."""
    current = share(current, set())
    protect_pinned(current.pinned, working)
    ids = {m.id for g in current.pinned for m in g.meanings}
    definitions = {m.id: m for g in working for m in g.meanings if m.id in ids}

    def revised(groups: Groups) -> Groups:
        return tuple(
            replace(g, meanings=tuple(definitions.get(m.id, m) for m in g.meanings)) for g in groups
        )

    return share(
        Dictionary(
            revised(current.pinned),
            {
                **{name: revised(gs) for name, gs in current.learned.items()},
                model: working,
            },
        ),
        ids,
    )


@dataclass(frozen=True)
class ProposalChange:
    id: str
    before: Group | None
    after: Group | None

    @property
    def kind(self) -> str:
        return "add" if self.before is None else "remove" if self.after is None else "update"

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "before": self.before.as_json() if self.before else None,
            "after": self.after.as_json() if self.after else None,
        }


@dataclass(frozen=True)
class Proposal:
    model: str
    working: Groups
    changes: tuple[ProposalChange, ...]
    version: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "model": self.model,
            "version": self.version,
            "changes": [change.as_json() for change in self.changes],
        }


def propose(current: Dictionary, proposed: Groups, model: str, version: str = "") -> Proposal:
    updated = refined(current, proposed, model)
    old = {g.id: g for g in current.effective(model)}
    new = {g.id: g for g in updated.effective(model)}
    return Proposal(
        model,
        tuple(new.values()),
        tuple(
            ProposalChange(identity, old.get(identity), new.get(identity))
            for identity in dict.fromkeys((*new, *old))
            if old.get(identity) != new.get(identity)
        ),
        version,
    )


def review(current: Dictionary, proposal: Proposal, selected: object = None) -> Dictionary:
    """Apply included proposals only; dismissal never removes existing knowledge."""
    changes = {c.id: c for c in proposal.changes}
    if selected is None:
        selected = [
            {"id": c.id, "after": c.after.as_json() if c.after else None} for c in proposal.changes
        ]
    working = {g.id: g for g in current.effective(proposal.model)}
    seen: set[str] = set()
    for item in _list(selected, "selected proposals"):
        item = _object(item, "proposal", {"id", "after"})
        identity = item.get("id")
        if not isinstance(identity, str) or identity not in changes or identity in seen:
            raise ValueError("Select each proposed change at most once by its current ID")
        seen.add(identity)
        change = changes[identity]
        if change.after is None:
            if item.get("after") is not None:
                raise ValueError("A removal can only be included or dismissed")
            working.pop(identity, None)
        else:
            groups = parse_groups([item.get("after")], "edited proposal")
            if groups[0].id != identity:
                raise ValueError("Keep the proposal's group ID while editing")
            working[identity] = groups[0]
    if working == {g.id: g for g in current.effective(proposal.model)}:
        return current
    return refined(current, tuple(working.values()), proposal.model)
