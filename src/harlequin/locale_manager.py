import locale

from harlequin.exception import HarlequinLocaleError, pretty_print_warning


def set_locale(locale_config: str) -> None:
    """
    Sets the locale based on the passed config or the system default. If the passed
    config is C (POSIX/Computer), no-ops. If the system default is C, tries to
    set "en_US" and prints a warning.
    """
    if locale_config and (locale_config == "C" or locale_config.split(".")[0] == "C"):
        return
    try:
        locale_result = locale.setlocale(locale.LC_ALL, locale_config)
    except locale.Error as e:
        raise HarlequinLocaleError(
            title="Could not set your locale",
            msg=(
                f"{e}: You likely need to install the locale "
                f"{locale_config} on your OS."
            ),
        ) from e

    if not locale_config and locale_result.split(".")[0] == "C":
        try:
            locale.setlocale(locale.LC_ALL, "en_US.UTF-8")
        except locale.Error:
            pretty_print_warning(
                title="System Locale Not Set",
                message=(
                    "Harlequin uses the locale of your device to format numbers.\n\n"
                    f"Your device's locale is set to {locale_result}, which is "
                    "a POSIX locale for computers, not humans.\n\nTo see thousands "
                    "separators in Harlequin, set your system locale, following "
                    "instructions for your OS, or pass a locale string to Harlequin "
                    "using the [bold]--locale[/] option. To suppress this warning, run "
                    "Harlequin with the [bold]--locale C[/] option.\n\n"
                    "See also https://harlequin.sh/docs/troubleshooting/locale"
                ),
            )
        else:
            pretty_print_warning(
                title="System Locale Not Set",
                message=(
                    "Harlequin uses the locale of your device to format numbers.\n\n"
                    f"Your device's locale is set to {locale_result}, which is "
                    "a POSIX locale for computers, not humans. We assume you are a "
                    "human and want to see thousands separators, so we set your "
                    "locale to en_US.UTF-8.\n\nTo configure a different locale or to "
                    "suppress this warning, set your system locale, following "
                    "instructions for your OS, or pass a locale string to Harlequin "
                    "using the [bold]--locale[/] option. To use Harlequin with the C "
                    "locale, Harlequin with the [bold]--locale C[/] option.\n\n"
                    "See also https://harlequin.sh/docs/troubleshooting/locale"
                ),
            )
