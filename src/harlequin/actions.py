from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual_textarea.text_editor import TextAreaPlus

from harlequin.components import (
    CodeEditor,
    DataCatalog,
    EditorCollection,
    HarlequinTree,
    HistoryScreen,
    ResultsViewer,
)

if TYPE_CHECKING:
    from textual.widget import Widget


@dataclass
class Action:
    target: type[Widget] | None
    action: str
    description: str | None = None
    show: bool = False
    priority: bool = False


HARLEQUIN_ACTIONS = {
    #######################################################
    # APP ACTIONS
    #######################################################
    "quit": Action(
        target=None,
        action="quit",
        priority=True,
        show=True,
    ),
    "help": Action(
        target=None,
        action="show_help_screen",
        description="Help",
        show=True,
    ),
    "focus_query_editor": Action(
        target=None,
        action="focus_query_editor",
    ),
    "focus_results_viewer": Action(
        target=None,
        action="focus_results_viewer",
    ),
    "focus_data_catalog": Action(
        target=None,
        action="focus_data_catalog",
    ),
    "toggle_sidebar": Action(
        target=None,
        action="toggle_sidebar",
    ),
    "toggle_full_screen": Action(
        target=None,
        action="toggle_full_screen",
    ),
    "show_query_history": Action(
        target=None,
        action="show_query_history",
        description="History",
        show=True,
    ),
    "show_data_exporter": Action(
        target=None, action="export", description="Export Data"
    ),
    "refresh_catalog": Action(
        target=None, action="refresh_catalog", description="Refresh Data Catalog"
    ),
    #######################################################
    # EditorCollection ACTIONS
    #######################################################
    "editor_collection.new_buffer": Action(
        target=EditorCollection, action="new_buffer"
    ),
    "editor_collection.close_buffer": Action(
        target=EditorCollection, action="close_buffer"
    ),
    "editor_collection.next_buffer": Action(
        target=EditorCollection, action="next_buffer"
    ),
    #######################################################
    # CodeEditor ACTIONS
    #######################################################
    "code_editor.run_query": Action(
        target=CodeEditor, action="submit", description="Run Query", show=True
    ),
    "code_editor.format_buffer": Action(
        target=CodeEditor, action="format", description="Format Query", show=True
    ),
    "code_editor.save_buffer": Action(
        target=CodeEditor, action="save", description="Save Query", show=True
    ),
    "code_editor.load_buffer": Action(
        target=CodeEditor, action="load", description="Open Query", show=True
    ),
    "code_editor.find": Action(
        target=CodeEditor, action="find", description="Find", show=True
    ),
    "code_editor.find_next": Action(
        target=CodeEditor, action="find(True)", description="Find Next", show=True
    ),
    "code_editor.goto_line": Action(
        target=CodeEditor, action="goto_line", description="Go To Line", show=True
    ),
    # Cursor movement
    "code_editor.cursor_up": Action(target=TextAreaPlus, action="cursor_up"),
    "code_editor.cursor_down": Action(target=TextAreaPlus, action="cursor_down"),
    "code_editor.cursor_left": Action(target=TextAreaPlus, action="cursor_left"),
    "code_editor.cursor_right": Action(target=TextAreaPlus, action="cursor_right"),
    "code_editor.cursor_word_left": Action(
        target=TextAreaPlus, action="cursor_word_left"
    ),
    "code_editor.cursor_word_right": Action(
        target=TextAreaPlus, action="cursor_word_right"
    ),
    "code_editor.cursor_line_start": Action(
        target=TextAreaPlus, action="cursor_line_start"
    ),
    "code_editor.cursor_line_end": Action(
        target=TextAreaPlus, action="cursor_line_end"
    ),
    "code_editor.cursor_doc_start": Action(
        target=TextAreaPlus, action="cursor_doc_start"
    ),
    "code_editor.cursor_doc_end": Action(target=TextAreaPlus, action="cursor_doc_end"),
    "code_editor.cursor_page_up": Action(target=TextAreaPlus, action="cursor_page_up"),
    "code_editor.cursor_page_down": Action(
        target=TextAreaPlus, action="cursor_page_down"
    ),
    # Selecting
    "code_editor.select_up": Action(target=TextAreaPlus, action="cursor_up(True)"),
    "code_editor.select_down": Action(target=TextAreaPlus, action="cursor_down(True)"),
    "code_editor.select_left": Action(target=TextAreaPlus, action="cursor_left(True)"),
    "code_editor.select_right": Action(
        target=TextAreaPlus, action="cursor_right(True)"
    ),
    "code_editor.select_word_left": Action(
        target=TextAreaPlus, action="cursor_word_left(True)"
    ),
    "code_editor.select_word_right": Action(
        target=TextAreaPlus, action="cursor_word_right(True)"
    ),
    "code_editor.select_line_start": Action(
        target=TextAreaPlus, action="cursor_line_start(True)"
    ),
    "code_editor.select_line_end": Action(
        target=TextAreaPlus, action="cursor_line_end(True)"
    ),
    "code_editor.select_doc_start": Action(
        target=TextAreaPlus, action="cursor_doc_start(True)"
    ),
    "code_editor.select_doc_end": Action(
        target=TextAreaPlus, action="cursor_doc_end(True)"
    ),
    "code_editor.select_word": Action(target=TextAreaPlus, action="select_word"),
    "code_editor.select_line": Action(target=TextAreaPlus, action="select_line"),
    "code_editor.select_all": Action(target=TextAreaPlus, action="select_all"),
    # Scrolling (No cursor movement)
    "code_editor.scroll_up_one": Action(target=TextAreaPlus, action="scroll_one('up')"),
    "code_editor.scroll_down_one": Action(
        target=TextAreaPlus, action="scroll_one('down')"
    ),
    # Bulk Editing
    "code_editor.toggle_comment": Action(target=TextAreaPlus, action="toggle_comment"),
    "code_editor.cut": Action(target=TextAreaPlus, action="cut"),
    "code_editor.copy": Action(target=TextAreaPlus, action="copy"),
    "code_editor.paste": Action(target=TextAreaPlus, action="paste"),
    "code_editor.undo": Action(target=TextAreaPlus, action="undo"),
    "code_editor.redo": Action(target=TextAreaPlus, action="redo"),
    # Deleting
    "code_editor.delete_left": Action(target=TextAreaPlus, action="delete_left"),
    "code_editor.delete_right": Action(target=TextAreaPlus, action="delete_right"),
    "code_editor.delete_word_left": Action(
        target=TextAreaPlus, action="delete_word_left"
    ),
    "code_editor.delete_word_right": Action(
        target=TextAreaPlus, action="delete_word_right"
    ),
    "code_editor.delete_line": Action(target=TextAreaPlus, action="delete_line"),
    "code_editor.delete_to_start_of_line": Action(
        target=TextAreaPlus, action="delete_to_start_of_line"
    ),
    "code_editor.delete_to_end_of_line": Action(
        target=TextAreaPlus, action="delete_to_end_of_line"
    ),
    #######################################################
    # DataCatalog ACTIONS
    #######################################################
    "data_catalog.previous_tab": Action(
        target=DataCatalog, action="switch_tab(-1)", description="Previous Tab"
    ),
    "data_catalog.next_tab": Action(
        target=DataCatalog, action="switch_tab(1)", description="Next Tab"
    ),
    "data_catalog.insert_name": Action(
        target=HarlequinTree, action="submit", description="Insert Name", show=True
    ),
    "data_catalog.copy_name": Action(
        target=HarlequinTree, action="copy", description="Copy Name"
    ),
    #######################################################
    # ResultsViewer ACTIONS
    #######################################################
    "results_viewer.previous_tab": Action(
        target=ResultsViewer, action="switch_tab(-1)", description="Previous Tab"
    ),
    "results_viewer.next_tab": Action(
        target=ResultsViewer, action="switch_tab(1)", description="Next Tab"
    ),
    #######################################################
    # HistoryScreen ACTIONS
    #######################################################
    "history_screen.select_query": Action(
        target=HistoryScreen, action="select", description="Select Query"
    ),
    "history_screen.cancel": Action(
        target=HarlequinTree, action="cancel", description="Cancel"
    ),
}
