"""
vim_text_editor.py

A drop-in TextEditor subclass that substitutes VimTextAreaPlus for
TextAreaPlus. This is the actual pattern harlequin.components.code_editor's
CodeEditor should use -- see integration_guide.md for the real diff
against Harlequin's own CodeEditor(TextEditor) class.

Kept as its own small file/class (rather than folding straight into
CodeEditor) so it can be tested against a *real* TextEditor container in
isolation first, the same "build it standalone, test it, then integrate"
approach used for the original vim_textarea.py.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label
from textual_textarea import TextEditor
from textual_textarea.autocomplete import CompletionList
from textual_textarea.containers import FooterContainer, TextContainer

from textual_vim_textarea.textarea_plus import VimTextAreaPlus


class VimTextEditor(TextEditor):
    def compose(self) -> ComposeResult:
        self.text_container = TextContainer()
        self.text_input = VimTextAreaPlus(
            language=self._language, text=self._initial_text, read_only=self.read_only
        )
        self.completion_list = CompletionList()
        self.footer = FooterContainer(classes="hide")
        self.footer_label = Label("", id="textarea__save_open_input_label")
        with self.text_container:
            yield self.text_input
            yield self.completion_list
        with self.footer:
            yield self.footer_label
