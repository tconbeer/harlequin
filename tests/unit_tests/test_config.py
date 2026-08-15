from __future__ import annotations

from pathlib import Path

import pytest

from harlequin.config import (
    ConfigFile,
    Profile,
    _find_config_files,
    get_config_for_profile,
    get_highest_priority_existing_config_file,
    get_profile,
    load_config,
    merge_profile_with_cli,
    parse_row_count,
    validate_profile_options,
)
from harlequin.exception import HarlequinConfigError
from harlequin.keymap import HarlequinKeyBinding, HarlequinKeyMap
from harlequin.options import FlagOption, SelectOption, TextOption


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_config(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    good_config = load_config(config_path=good_config_path)
    assert isinstance(good_config, dict)
    assert "default_profile" in good_config
    assert good_config["default_profile"] == "my-duckdb-profile"
    assert "profiles" in good_config
    expected_profiles = ["my-duckdb-profile", "local-postgres"]
    assert all(name in good_config["profiles"] for name in expected_profiles)
    assert all(
        isinstance(good_config["profiles"][name], dict) for name in expected_profiles
    )
    assert good_config["profiles"]["my-duckdb-profile"]["limit"] == 200_000


def test_load_keymap(data_dir: Path) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / "keymaps.toml"
    keymap_name = "more_arrows"
    good_config = load_config(config_path=good_config_path)
    assert isinstance(good_config, dict)
    assert "keymaps" in good_config
    assert isinstance(good_config["keymaps"], dict)
    assert keymap_name in good_config["keymaps"]
    assert isinstance(good_config["keymaps"][keymap_name], list)
    assert isinstance(good_config["keymaps"][keymap_name][0], dict)
    assert all(
        [
            k in good_config["keymaps"][keymap_name][0]
            for k in ["keys", "action", "key_display"]
        ]
    )

    _, keymaps = get_config_for_profile(config_path=good_config_path, profile_name=None)
    assert len(keymaps) == 1
    assert isinstance(keymaps[0], HarlequinKeyMap)
    assert keymaps[0].name == keymap_name
    assert len(keymaps[0].bindings) == 4
    assert isinstance(keymaps[0].bindings[0], HarlequinKeyBinding)


@pytest.mark.parametrize(
    "filename,key_words",
    [
        ("keymaps_bad_binding.toml", ["Key bindings", "foo"]),
    ],
)
def test_load_bad_keymap_raises(
    data_dir: Path,
    filename: str,
    key_words: list[str],
) -> None:
    config_path = data_dir / "unit_tests" / "config" / filename
    with pytest.raises(HarlequinConfigError) as exc_info:
        _ = get_config_for_profile(config_path=config_path, profile_name=None)
    err = exc_info.value
    print(err)
    assert isinstance(err, HarlequinConfigError)
    assert "keymap" in err.title
    assert all([w in err.msg for w in key_words])


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_named_profile(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    profile, keymaps = get_config_for_profile(
        config_path=good_config_path, profile_name="local-postgres"
    )
    assert profile["port"] == 5432  # type: ignore[typeddict-item]
    assert profile["theme"] == "fruity"


@pytest.mark.parametrize("filename", ["good_config.toml", "pyproject.toml"])
def test_load_default_profile(data_dir: Path, filename: str) -> None:
    good_config_path = data_dir / "unit_tests" / "config" / filename
    profile, keymaps = get_config_for_profile(
        config_path=good_config_path, profile_name=None
    )
    assert profile["adapter"] == "duckdb"
    assert profile["theme"] == "monokai"


@pytest.mark.parametrize(
    "filename,key_words",
    [
        ("default_no_exist.toml", ["default_profile", "foo"]),
        # a file that names a default and defines no profiles at all used to
        # escape as a KeyError rather than being reported
        ("default_no_profiles.toml", ["default_profile", "foo"]),
        ("extra_key.toml", ["unexpected key"]),
        ("none_profile.toml", ["None", "not allowed"]),
        ("not_toml.toml", ["Attempted to load"]),
        ("profiles_not_table.toml", ["profiles", "table"]),
        ("profile_not_table.toml", ["profiles.foo", "table"]),
        ("bad_option_name.toml", ["option", "invalid", "read-only", "read_only"]),
        ("keymaps_not_array.toml", ["keymaps", "array"]),
    ],
)
def test_bad_config_raises(
    data_dir: Path,
    filename: str,
    key_words: list[str],
) -> None:
    config_path = data_dir / "unit_tests" / "config" / filename
    with pytest.raises(HarlequinConfigError) as exc_info:
        _ = load_config(config_path=config_path)
    err = exc_info.value
    assert isinstance(err, HarlequinConfigError)
    assert "config" in err.title
    assert all([w in err.msg for w in key_words])
    # every message names the file the key is written in, which is what
    # validating each file ahead of the merge is for
    assert filename in err.msg


def test_config_file_discovery(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # first, patch the real search paths with tmps
    mock_home = tmp_path_factory.mktemp("home")
    mock_config = tmp_path_factory.mktemp("config")
    mock_cwd = tmp_path_factory.mktemp("cwd")
    custom = tmp_path_factory.mktemp("custom") / "foo.toml"

    # create empty config files in our mock dirs, highest priority first
    expected_paths = [
        custom,
        mock_cwd / "harlequin.toml",
        mock_cwd / ".harlequin.toml",
        mock_cwd / "pyproject.toml",
        mock_config / "harlequin.toml",
        mock_config / "config.toml",
        mock_home / "harlequin.toml",
        mock_home / ".harlequin.toml",
        mock_home / "pyproject.toml",
    ]
    for p in expected_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("w").close()

    monkeypatch.setattr(Path, "cwd", lambda: mock_cwd)
    monkeypatch.setattr(Path, "home", lambda: mock_home)
    monkeypatch.setattr("harlequin.config.user_config_path", lambda **_: mock_config)

    assert _find_config_files(config_path=custom) == expected_paths

    expected_paths.pop(0)
    assert _find_config_files(config_path=None) == expected_paths
    assert get_highest_priority_existing_config_file() == expected_paths[0]


def test_merge_profile_with_cli_prefers_values_the_user_typed() -> None:
    profile: Profile = {"theme": "fruity", "limit": 200_000}
    merged = merge_profile_with_cli(
        profile=profile,
        cli_values={"theme": "zenburn", "limit": 100_000},
        explicitly_set={"theme"},
    )
    # the theme was typed, so it wins; the limit is a default, so it doesn't
    assert merged == {"theme": "zenburn", "limit": 200_000}


def test_merge_profile_with_cli_keeps_options_the_profile_alone_defines() -> None:
    """Adapter options live in the profile under names the CLI never saw."""
    profile: Profile = {"adapter": "postgres", "dbname": "prod"}  # type: ignore[typeddict-unknown-key]
    merged = merge_profile_with_cli(
        profile=profile,
        cli_values={"username": "ted"},
        explicitly_set={"username"},
    )
    assert merged == {"adapter": "postgres", "dbname": "prod", "username": "ted"}


def test_merge_profile_with_cli_ignores_an_empty_conn_str() -> None:
    """An absent conn_str would otherwise leave `harlequin -P prod` nothing to open."""
    profile: Profile = {"conn_str": ["my-database.db"]}
    merged = merge_profile_with_cli(
        profile=profile,
        cli_values={"conn_str": tuple()},
        explicitly_set={"conn_str"},
    )
    assert merged == {"conn_str": ["my-database.db"]}


def test_merge_profile_with_cli_accepts_a_conn_str_that_was_passed() -> None:
    profile: Profile = {"conn_str": ["my-database.db"]}
    merged = merge_profile_with_cli(
        profile=profile,
        cli_values={"conn_str": ("other.db",)},
        explicitly_set={"conn_str"},
    )
    assert merged == {"conn_str": ("other.db",)}


def test_merge_profile_with_cli_leaves_its_arguments_alone() -> None:
    """The profile is a live reference into the merged config files."""
    profile: Profile = {"theme": "fruity"}
    cli_values = {"theme": "zenburn"}
    merged = merge_profile_with_cli(
        profile=profile, cli_values=cli_values, explicitly_set={"theme"}
    )
    assert profile == {"theme": "fruity"}
    assert cli_values == {"theme": "zenburn"}
    assert merged is not profile


def test_merge_profile_with_cli_falsy_values_are_still_values() -> None:
    """`--limit 0` and `--no-init` mean what they say."""
    profile: Profile = {"limit": 200_000, "no_init": False}  # type: ignore[typeddict-unknown-key]
    merged = merge_profile_with_cli(
        profile=profile,
        cli_values={"limit": 0, "no_init": True},
        explicitly_set={"limit", "no_init"},
    )
    assert merged == {"limit": 0, "no_init": True}


class TestParseRowCount:
    """-1 is unlimited everywhere; 0 is zero rows, except for the viewer."""

    @pytest.mark.parametrize("value,expected", [(500, 500), ("500", 500), (0, 0)])
    def test_a_number_of_rows(self, value: object, expected: int) -> None:
        assert parse_row_count(value, key="limit") == expected

    def test_minus_one_is_unlimited(self) -> None:
        assert parse_row_count(-1, key="limit") is None

    def test_zero_is_unlimited_only_where_it_always_was(self) -> None:
        """A Results Viewer holding no rows serves nobody, so 0 keeps the
        meaning it has had there since before there was another key."""
        assert parse_row_count(0, key="viewer_max_rows", zero_is_unlimited=True) is None

    @pytest.mark.parametrize("value", ["all", None, 1.5, -2])
    def test_what_is_not_a_number_of_rows(self, value: object) -> None:
        with pytest.raises(HarlequinConfigError):
            parse_row_count(value, key="limit")


class TestConfigFileRoundTrip:
    """Reading and writing use different parsers, so writing has to be tested.

    `ConfigFile` reads with `tomllib` because every start-up reads config, and
    writes with tomlkit because a user's comments have to survive the write.
    Nothing catches the two drifting apart except these.
    """

    def test_a_write_preserves_the_comments_around_what_it_did_not_touch(
        self, tmp_path: Path
    ) -> None:
        """Which is why writing still goes through tomlkit.

        Note the limit, which is not new: `relevant_config` hands back plain
        data, so `update()` replaces a whole table rather than editing inside
        it, and comments *within* a rewritten table do not survive. Comments
        elsewhere in the file do.
        """
        path = tmp_path / ".harlequin.toml"
        path.write_text(
            "# a file someone wrote by hand\n"
            'default_profile = "one"\n'
            "\n"
            "# and a note further down\n"
            "[profiles.one]\n"
            'theme = "fruity"\n'
        )

        config_file = ConfigFile(path)
        config = config_file.relevant_config
        config["profiles"]["two"] = {"theme": "monokai"}
        config_file.update(config)
        config_file.write()

        written = path.read_text()
        assert "# a file someone wrote by hand" in written
        assert "# and a note further down" in written
        # and the new profile arrived, without disturbing the old one
        reread = load_config(config_path=path)
        assert reread["profiles"]["two"] == {"theme": "monokai"}
        assert reread["profiles"]["one"] == {"theme": "fruity"}
        assert reread["default_profile"] == "one"

    def test_a_write_to_pyproject_touches_only_the_harlequin_table(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "pyproject.toml"
        path.write_text(
            "[project]\n"
            'name = "someone-elses-project"\n'
            "\n"
            "[tool.harlequin.profiles.one]\n"
            'theme = "fruity"\n'
        )

        config_file = ConfigFile(path)
        config = config_file.relevant_config
        config["default_profile"] = "one"
        config_file.update(config)
        config_file.write()

        reread = ConfigFile(path)
        assert reread.relevant_config["default_profile"] == "one"
        assert reread.relevant_config["profiles"]["one"] == {"theme": "fruity"}
        # the rest of the file is not ours to rewrite
        assert 'name = "someone-elses-project"' in path.read_text()

    def test_a_write_creates_a_file_that_does_not_exist_yet(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "nested" / ".harlequin.toml"

        config_file = ConfigFile(path)
        assert config_file.relevant_config == {}
        config_file.update(
            {"default_profile": "one", "profiles": {"one": {"theme": "fruity"}}}
        )
        config_file.write()

        assert path.exists()
        assert load_config(config_path=path) == {
            "default_profile": "one",
            "profiles": {"one": {"theme": "fruity"}},
        }

    def test_a_write_adds_the_harlequin_table_to_a_pyproject_without_one(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\nname = "someone-elses-project"\n')

        config_file = ConfigFile(path)
        assert config_file.relevant_config == {}
        config_file.update({"default_profile": "one"})
        config_file.write()

        assert ConfigFile(path).relevant_config == {"default_profile": "one"}

    def test_an_unreadable_file_raises_before_anything_is_written(
        self, data_dir: Path
    ) -> None:
        with pytest.raises(HarlequinConfigError):
            ConfigFile(data_dir / "unit_tests" / "config" / "not_toml.toml")


@pytest.fixture
def config_dirs(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """A cwd and a home directory to write config files into, and nothing else.

    Returns them nearest first, which is the order the files in them are read.
    """
    mock_cwd = tmp_path_factory.mktemp("cwd")
    mock_home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(Path, "cwd", lambda: mock_cwd)
    monkeypatch.setattr(Path, "home", lambda: mock_home)
    monkeypatch.setattr("harlequin.config._search_config", list)
    return mock_cwd, mock_home


class TestMergingConfigFiles:
    """Later files merge into earlier ones per profile, not per table.

    Every case here is a config that two files disagree about, which is the
    only place the merge is visible at all.
    """

    def test_a_project_file_does_not_displace_the_profiles_in_a_home_file(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        """The failing case from #1040, which used to refuse to start.

        A `profiles` table used to replace another file's outright while the
        `default_profile` naming one of them survived, so the two contradicted
        each other and both commands exited 2.
        """
        cwd, home = config_dirs
        (home / ".harlequin.toml").write_text(
            'default_profile = "personal"\n'
            "[profiles.personal]\n"
            'adapter = "duckdb"\n'
            "[profiles.shared]\n"
            'adapter = "sqlite"\n'
        )
        (cwd / ".harlequin.toml").write_text('[profiles.project]\nadapter = "sqlite"\n')

        config = load_config(config_path=None)

        assert sorted(config["profiles"]) == ["personal", "project", "shared"]
        assert config["default_profile"] == "personal"
        assert get_profile(config_path=None, profile_name=None) == {"adapter": "duckdb"}

    def test_the_nearest_file_that_defines_a_profile_is_the_one_that_defines_it(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        """A profile is one file's, whole: its keys are not merged with another's.

        Half a connection from each of two files is not a connection either of
        them describes, and it is not something a reader of either can predict.
        """
        cwd, home = config_dirs
        (home / ".harlequin.toml").write_text(
            "[profiles.prod]\n"
            'adapter = "postgres"\n'
            'conn_str = ["postgres://prod"]\n'
            "[profiles.other]\n"
            "limit = 10\n"
        )
        (cwd / ".harlequin.toml").write_text("[profiles.prod]\nlimit = 100\n")

        assert get_profile(config_path=None, profile_name="prod") == {"limit": 100}
        assert get_profile(config_path=None, profile_name="other") == {"limit": 10}

    def test_the_nearest_file_that_names_a_default_is_the_one_that_names_it(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        cwd, home = config_dirs
        (home / ".harlequin.toml").write_text(
            'default_profile = "personal"\n[profiles.personal]\nlimit = 1\n'
        )
        (cwd / ".harlequin.toml").write_text(
            'default_profile = "project"\n[profiles.project]\nlimit = 2\n'
        )

        assert load_config(config_path=None)["default_profile"] == "project"
        assert get_profile(config_path=None, profile_name=None) == {"limit": 2}

    def test_a_default_named_in_one_file_can_be_defined_in_another(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        """And the nearer file's copy of it still wins.

        The name is not known until the file that sets `default_profile` is
        read, so the files read before it are held rather than skipped.
        """
        cwd, home = config_dirs
        (home / ".harlequin.toml").write_text(
            'default_profile = "prod"\n[profiles.prod]\nlimit = 1\n'
        )
        (cwd / ".harlequin.toml").write_text("[profiles.prod]\nlimit = 2\n")

        assert get_profile(config_path=None, profile_name=None) == {"limit": 2}

    def test_keymaps_merge_by_name_too(self, config_dirs: tuple[Path, Path]) -> None:
        cwd, home = config_dirs
        (home / ".harlequin.toml").write_text(
            '[[keymaps.mine]]\nkeys = "w"\naction = "results_viewer.cursor_up"\n'
            '[[keymaps.theirs]]\nkeys = "s"\naction = "results_viewer.cursor_down"\n'
        )
        (cwd / ".harlequin.toml").write_text(
            '[[keymaps.mine]]\nkeys = "a"\naction = "results_viewer.cursor_left"\n'
        )

        _, keymaps = get_config_for_profile(config_path=None, profile_name=None)

        by_name = {keymap.name: keymap for keymap in keymaps}
        assert sorted(by_name) == ["mine", "theirs"]
        assert by_name["mine"].bindings[0].keys == "a"

    def test_an_explicit_config_path_outranks_every_discovered_file(
        self, config_dirs: tuple[Path, Path], tmp_path: Path
    ) -> None:
        cwd, home = config_dirs
        (cwd / ".harlequin.toml").write_text("[profiles.prod]\nlimit = 1\n")
        explicit = tmp_path / "explicit.toml"
        explicit.write_text("[profiles.prod]\nlimit = 2\n")

        assert get_profile(config_path=explicit, profile_name="prod") == {"limit": 2}


class TestReadingNoMoreThanItMust:
    """Files are read nearest first, so the far ones often need not be read.

    Each of these puts something unreadable in a file that must not be reached
    -- a file that is opened at all takes the test down with it.
    """

    def test_a_profile_found_near_stops_the_search(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        cwd, home = config_dirs
        (cwd / ".harlequin.toml").write_text("[profiles.prod]\nlimit = 1\n")
        (home / ".harlequin.toml").write_text("this is not toml at all [[[")

        assert get_profile(config_path=None, profile_name="prod") == {"limit": 1}

    def test_the_default_profile_stops_the_search_too(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        cwd, home = config_dirs
        (cwd / ".harlequin.toml").write_text(
            'default_profile = "prod"\n[profiles.prod]\nlimit = 1\n'
        )
        (home / ".harlequin.toml").write_text("this is not toml at all [[[")

        assert get_profile(config_path=None, profile_name=None) == {"limit": 1}

    def test_the_profile_named_None_reads_nothing_at_all(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        """`-P None` asks for Harlequin's defaults, and that is answerable here."""
        cwd, _ = config_dirs
        (cwd / ".harlequin.toml").write_text("this is not toml at all [[[")

        assert get_profile(config_path=None, profile_name="None") == {}

    def test_a_file_it_did_reach_is_still_validated(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        cwd, home = config_dirs
        (cwd / ".harlequin.toml").write_text("[profiles.prod]\nlimit = true\n")
        (home / ".harlequin.toml").write_text("this is not toml at all [[[")

        with pytest.raises(HarlequinConfigError) as exc_info:
            get_profile(config_path=None, profile_name="prod")
        assert "limit" in exc_info.value.msg

    def test_reading_the_whole_config_still_reads_the_whole_config(
        self, config_dirs: tuple[Path, Path]
    ) -> None:
        """`load_config()` is the document, so it has no reason to stop early."""
        cwd, home = config_dirs
        (cwd / ".harlequin.toml").write_text("[profiles.prod]\nlimit = 1\n")
        (home / ".harlequin.toml").write_text("this is not toml at all [[[")

        with pytest.raises(HarlequinConfigError):
            load_config(config_path=None)


class TestValidatingAgainstAnAdapter:
    """The second pass: a profile's keys against the options its adapter declares.

    Nothing else in the stack can do this -- an adapter takes supersets of what
    it declares, so a key it has never heard of reaches its constructor and is
    dropped there in silence.
    """

    OPTIONS = [
        FlagOption(name="read_only", description="x"),
        TextOption(name="port", description="x"),
        SelectOption(name="mode", description="x", choices=["ro", "rw"]),
    ]

    def test_a_misspelled_option_is_refused_and_the_spelling_suggested(self) -> None:
        with pytest.raises(HarlequinConfigError) as exc_info:
            validate_profile_options(
                {"reed_only": True},  # type: ignore[typeddict-unknown-key]
                adapter="duckdb",
                options=self.OPTIONS,
            )
        message = exc_info.value.msg
        assert "reed_only" in message
        assert "read_only" in message
        assert "duckdb" in message

    @pytest.mark.parametrize(
        "profile",
        [
            {"read_only": True, "port": 5432, "mode": "ro"},
            {"limit": 100, "conn_str": ["my.db"]},  # keys a command owns
            {"theme": "fruity", "keymap_name": ["vscode"]},  # keys the IDE owns
            {"port": "5432"},  # the contract promises values may arrive uncast
            {"mode": "RO"},  # click matches choices case-insensitively
        ],
    )
    def test_what_a_profile_may_hold(self, profile: Profile) -> None:
        validate_profile_options(profile, adapter="duckdb", options=self.OPTIONS)

    def test_a_value_outside_the_declared_choices_is_refused(self) -> None:
        with pytest.raises(HarlequinConfigError) as exc_info:
            validate_profile_options(
                {"mode": "reed-only"},  # type: ignore[typeddict-unknown-key]
                adapter="duckdb",
                options=self.OPTIONS,
            )
        message = exc_info.value.msg
        assert "mode" in message
        assert "'ro'" in message and "'rw'" in message

    def test_an_adapter_that_declares_nothing_still_takes_the_keys_core_owns(
        self,
    ) -> None:
        validate_profile_options({"limit": 100}, adapter="fake", options=None)
        with pytest.raises(HarlequinConfigError):
            validate_profile_options(
                {"anything": 1},  # type: ignore[typeddict-unknown-key]
                adapter="fake",
                options=None,
            )
