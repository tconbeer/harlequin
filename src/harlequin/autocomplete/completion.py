from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(order=False)
class HarlequinCompletion:
    """
    A HarlequinCompletion represents a single item in the autocomplete drop-down list.

    Harlequin will concatenate and style the label and type_label and show them in
    the autocomplete list. When a user selects an autocomplete item, Harlequin will
    insert the completion's value into the editor. Completions are ordered by their
    priority (lowest first). Harlequin gives reserved keywords a priority of 100,
    Catalog items a priority of 500, and functions a priority of 1000. Completions
    can have a context, which will cause them to be shown as member completions after
    the user types the context followed by a `.` or `:`.

    Args:
        label (str): The text shown to the user in the autocomplete menu.
        type_label (str): Dimmend text shown to the user next to the label in the
            autocomplete menu.
        value (str): The text inserted into the query editor when the completion is
            selected.
        priority (int): The sort order of the completion (lowest first). Harlequin
            uses 100 for reserved keywords and 1000 for functions.
        context (str | None): A namespace for the completion. Defaults to None.
    """

    label: str
    type_label: str
    value: str
    priority: int
    context: str | None = None

    def __lt__(self, other: "HarlequinCompletion") -> bool:
        return (self.priority, self.label) < (other.priority, other.label)

    def __le__(self, other: "HarlequinCompletion") -> bool:
        return (self.priority, self.label) <= (other.priority, other.label)

    def __gt__(self, other: "HarlequinCompletion") -> bool:
        return (self.priority, self.label) > (other.priority, other.label)

    def __ge__(self, other: "HarlequinCompletion") -> bool:
        return (self.priority, self.label) >= (other.priority, other.label)

    @cached_property
    def match_val(self) -> str:
        return self.label.lower()
