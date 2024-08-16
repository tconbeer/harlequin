from pygments.styles import get_all_styles

from harlequin.colors import HarlequinColors


def test_all_styles() -> None:
    for style in get_all_styles():
        print(style)
        colors = HarlequinColors.from_theme(style)
        assert colors
        assert colors.background
        assert colors.highlight
        assert colors.text
        assert colors.primary
        assert colors.secondary
        assert colors.gray
        assert colors.error
        assert colors.text != colors.background
        assert colors.highlight != colors.background
        assert colors.color_system
