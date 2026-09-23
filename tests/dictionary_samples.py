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


def proposed(text: str = "I use cloud code.") -> dict[str, Any]:
    from entune.llm import sources

    record = group("Claude Code", "cloud code").as_json()
    record["id"] = "new_group"
    record["meanings"][0]["id"] = "new_meaning"
    for form in record["recognized_forms"]:
        link = form["associations"][0]
        link["meaning_id"] = "new_meaning"
        if form["text"] == "cloud code":
            start = text.index("cloud code")
            link.update(
                basis="text",
                evidence=[
                    {"source": next(iter(sources([text]))), "start": start, "end": start + 10}
                ],
            )
    return {"additions": [record]}


def document(*pinned: Group, learned: dict[str, tuple[Group, ...]] | None = None) -> dict[str, Any]:
    """A dictionary.json body in the current format."""
    return {
        "version": 2,
        "pinned": [g.as_json() for g in pinned],
        "learned": {
            model: [g.as_json() for g in groups] for model, groups in (learned or {}).items()
        },
    }
