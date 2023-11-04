from textual.widgets import Input as Input
from textual.widgets import Label
from textual.widgets import Select as Select
from textual.widgets import Switch as Switch
from textual_textarea import PathInput as PathInput


class NoFocusLabel(Label, can_focus=False):
    pass
