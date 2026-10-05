"""Small explicit fixtures shared by dictionary, transport, generation and API tests."""

from typing import Any

from entune.dictionary.entries import Association, Form, Group, Meaning


def group(spelling: str, heard: str, *, literal: str | None = None, direct: bool = False) -> Group:
    token = "".join(c for c in spelling.lower() if c.isalnum())
    target = Meaning(f"a_{token}", spelling, f"The named tool {spelling}.")
    meanings: tuple[Meaning, ...] = (target,)
    links: tuple[Association, ...] = (Association(target.id),)
    if literal:
        other = Meaning(f"b_{token}", heard, literal, casing="ordinary")
        meanings += (other,)
        links += (Association(other.id, basis="literal"),)
    return Group(
        f"g_{token}",
        meanings,
        (
            Form(
                heard,
                links,
                target.id if direct else None,
                "Explicitly approved in this test" if direct else "",
            ),
            Form(spelling, (Association(target.id, basis="literal"),)),
        ),
    )


JEV = Group(
    "g_jev",
    (
        Meaning("a_jev", "Jev", "TypeSafe's contextual decision model.", "Used in Entune."),
        Meaning("b_jeff", "Jeff", "A person's given name."),
        Meaning("c_gif", "GIF", "An image format for still or animated images."),
    ),
    (
        Form("Jeff", (Association("a_jev"), Association("b_jeff", basis="literal"))),
        Form("GIF", (Association("a_jev"), Association("c_gif", basis="literal"))),
        Form("Jif", (Association("a_jev"), Association("c_gif"))),
        Form("Jev", (Association("a_jev", basis="literal"),)),
    ),
)
CLOUD = Group(
    "g_cloud",
    (
        Meaning("a_claude", "Claude", "Anthropic's AI assistant."),
        Meaning("b_cloud", "cloud", "Remote computing infrastructure.", casing="ordinary"),
        Meaning("c_cloud", "cloud", "A visible cloud in the sky.", casing="ordinary"),
    ),
    (
        Form(
            "cloud",
            (
                Association("a_claude"),
                Association("b_cloud", basis="literal"),
                Association("c_cloud", basis="literal"),
            ),
        ),
        Form("Claude", (Association("a_claude", basis="literal"),)),
    ),
)


def proposed(text: str = "I use cloud code.", dictation: str = "d1") -> dict[str, Any]:
    """A reply adding Claude Code, heard as "cloud code" in `dictation`, whose text is `text`."""
    start = text.index("cloud code")
    meaning = {
        "id": "n1",
        "spelling": "Claude Code",
        "meaning": "The named tool Claude Code.",
        "casing": "fixed",
    }
    evidence = {"dictation": dictation, "start": start, "end": start + 10}
    heard = [
        {
            "text": "cloud code",
            "links": [{"meaning": "n1", "basis": "text", "evidence": [evidence]}],
        },
        {"text": "Claude Code", "links": [{"meaning": "n1", "basis": "literal", "evidence": []}]},
    ]
    return {"additions": [{"meanings": [meaning], "heard": heard}], "revisions": [], "removals": []}


def document(*pinned: Group, learned: dict[str, tuple[Group, ...]] | None = None) -> dict[str, Any]:
    """A dictionary.json body in the current format."""
    return {
        "version": 2,
        "pinned": [g.as_json() for g in pinned],
        "learned": {
            model: [g.as_json() for g in groups] for model, groups in (learned or {}).items()
        },
    }
