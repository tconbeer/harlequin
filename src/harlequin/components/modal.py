from __future__ import annotations

from textual.await_complete import AwaitComplete
from textual.screen import ModalScreen, ScreenResultType


class HarlequinModal(ModalScreen[ScreenResultType]):
    """Base for Harlequin's modals, which only dismiss themselves from the top.

    Textual's `dismiss()` spends this screen's result and then pops whatever
    screen is on top, so an event this modal received before another covered it
    (an error modal pushed by a worker, say) would pop that one instead, and the
    next dismiss would raise `InvalidStateError`.
    """

    def dismiss(self, result: ScreenResultType | None = None) -> AwaitComplete:
        if not self.is_active:
            return AwaitComplete()
        return super().dismiss(result)
