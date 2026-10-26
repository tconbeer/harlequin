# Keymaps, editor modes, and connections

The author's VS Code keymap is unchanged. It is still what Harlequin loads when
nothing else is named. Everything in this document is a plugin a session can
load, or leave unloaded.

`keymap_name` on a profile is the list that profile starts with. Repeating
`--keymap-name` does the same thing from the command line, and a later name is
applied after an earlier one. Saving a connection writes the list that is
loaded at that moment into the new profile, so the keymaps chosen for the
session are the ones that profile starts with.

The command palette (`ctrl+p` on the VS Code keymap) switches without a
restart:

| Command | What it loads |
| --- | --- |
| Keymap: VS Code | `vscode` |
| Keymap: VS Code and Files | `vscode`, then `files` |
| Keymap: Vim | `vim` |
| Keymap: Emacs | `emacs` |

A name that is not installed is reported and the keys stay as they are. Loading
a keymap again removes the bindings the previous load added and applies the new
list once. Class bindings that shipped with a widget are left in place. `ctrl+q`
stays quit when a map does not bind quit itself.

Vim and Emacs also change how the query editor reads keys. That mode runs only
while the editor's text area is focused on the main screen. A dialog keeps
Escape. Find, the data catalog, and the results viewer keep the keymap's
bindings, because those widgets are not the query text area.

## Files

Load this after VS Code. None of these keys are in that keymap on the same
widget. The footer stays quiet; `f7` and the command palette show the keys.

| Keys | Action |
| --- | --- |
| `ctrl+shift+e` | Show Files |
| `ctrl+shift+o` | Open Folder. `ctrl+o` stays Open Query |
| `ctrl+alt+o` | Open File |
| `ctrl+shift+w` | Close Folder. `ctrl+w` stays Close Buffer |
| `ctrl+alt+n` | New File. `ctrl+n` stays New Buffer |
| `ctrl+alt+shift+n` | New Folder |
| `ctrl+alt+r` | Rename the file or folder under the cursor |
| `ctrl+alt+d` | Delete the file or folder under the cursor |
| `ctrl+alt+s` | Show S3 |
| `ctrl+pageup` | Previous buffer |
| `ctrl+pagedown` | Next buffer. `ctrl+k` stays next buffer too |
| `ctrl+shift+k` | Close all buffers |
| `ctrl+alt+t` | Toggle transaction mode |
| `ctrl+alt+enter` | Commit |
| `ctrl+alt+backspace` | Roll back |
| `ctrl+alt+c` | Cancel the running query |
| `ctrl+alt+comma` | Change theme |
| `alt+1` | Focus the query editor |
| `alt+2` | Focus the results |
| `alt+3` | Focus the data catalog |
| `alt+4` | Query history |
| `alt+5` | Export |
| `alt+0` | Show or hide the sidebar |

`alt+1` through `alt+0` are priority bindings, so the editor does not insert
the digit. Commit, rollback, cancel, the transaction toggle, and the theme
are priority too, so they run while the editor is focused.

## Vim

`vim` is a full keymap: the VS Code bindings, the Files bindings, and a mode
in the query editor. It starts in insert mode, so a query still types and
`ctrl+s` still saves. Escape enters normal mode. A key normal mode does not
know is swallowed, so it is not inserted into the query.

Normal mode:

| Keys | Action |
| --- | --- |
| `h` `j` `k` `l` | Left, down, up, right |
| `w` `b` | Next word, previous word |
| `0` `$` | Start of line, end of line |
| `gg` | Start of the buffer |
| `G` | End of the buffer. A count, as in `3G`, is that line |
| `ctrl+f` `ctrl+b` | Page down, page up |
| `ctrl+d` `ctrl+u` | Page down, page up |
| `ctrl+r` | Redo. In insert mode, `ctrl+r` still refreshes the catalog |
| `u` | Undo |
| `x` | Delete the character under the cursor |
| `p` | Paste |
| `dd` `dw` `d$` `d0` | Delete the line, the word, to the end, or to the start |
| `yy` `yw` | Copy the line, or the word |
| `v` | Visual mode. A motion then selects. `v` again leaves it |
| `V` | Select the line |
| `i` `a` `A` `I` | Insert, insert after the cursor, at the end, at the start |
| `/` | Find |
| `n` | Find next |
| A count | Repeats the next motion, up to 99 |

Space, in normal mode, is a leader. The next key runs one command:

| Key | Action |
| --- | --- |
| `q` | Run the query |
| `w` or `s` | Save |
| `e` | Focus the editor |
| `r` | Focus the results |
| `c` | Focus the catalog |
| `h` | History |
| `x` | Export |
| `n` | New buffer |
| `d` | Close the buffer |
| `o` | Open File |
| `f` | Open Folder |
| `F` | Show Files |
| `b` | Show or hide the sidebar |
| `t` | Next buffer |
| `T` | Previous buffer |
| `g` | Go to line |
| `/` | Find |
| `?` | Help |

`ctrl+w` stays Close Buffer, from the VS Code bindings this map includes.
`h` `j` `k` `l` are not bindings, so insert mode types them.

## Emacs

Load `emacs` on its own. It is a full keymap, and where it uses a VS Code key
for something else, that is the point of choosing it. Merging it after VS Code
would leave the VS Code binding in front.

In the query editor:

| Keys | Action |
| --- | --- |
| `ctrl+a` `ctrl+e` | Start of line, end of line |
| `ctrl+b` `ctrl+f` | Left, right. Find moves to `ctrl+s`. `f3` still finds the next match |
| `ctrl+p` `ctrl+n` | Up, down. `alt+x` opens the command palette from anywhere |
| `ctrl+d` | Delete the character under the cursor |
| `ctrl+k` | Delete to the end of the line |
| `ctrl+y` | Paste |
| `ctrl+w` | Cut. Close Buffer is `ctrl+x k` |
| `alt+f` `alt+b` | Next word, previous word |
| `alt+d` `alt+backspace` | Delete the next word, or the previous word |
| `ctrl+/` or `ctrl+z` | Undo |
| `ctrl+shift+z` | Redo |
| `alt+g` | Go to line |
| `ctrl+pageup` `ctrl+pagedown` | Previous buffer, next buffer |

`ctrl+x` starts a prefix. The next key runs one command. Escape, `ctrl+g`, or
a key the prefix does not know cancels it and does not insert that key.

| Second key | Action |
| --- | --- |
| `s` or `ctrl+s` | Save |
| `c` or `ctrl+c` | Quit. `ctrl+q` also quits |
| `f` or `ctrl+f` | Open File |
| `k` or `ctrl+k` | Close the buffer |
| `b` | Next buffer |
| `p` | Previous buffer |
| `n` | New buffer |
| `e` | Export |
| `h` | Select all |
| `u` | Undo |
| `r` | Redo |

The data catalog, the results viewer, and query history keep the VS Code keys.
The Files keys from the table above are included.

## Connections

One session can hold eight connections. The ninth open is refused, and the
connection it had just made is closed, so a full shelf does not leak. The last
open connection cannot be closed. Quit closes every slot once, including a
connection the shelf and the app both point at.

A new connection is opened on its own worker. That worker is not the one that
opens the first database: a failure there shows an error and leaves the session
up. Switching is refused while a query is running. The catalog is reloaded
from the connection that became active. Query history keeps one store and
records later rows against the connection that ran them.

The connection list shows the adapter and the connection string with
credentials masked. Saving a profile does not repeat the connection string in
the notification. The profile is written to the user config file,
`config.toml`, beside any profiles already there. Its name is letters,
numbers, underscores, and hyphens, and it is not `None`.

| Command | What it does |
| --- | --- |
| Open Connection | Another database, on an installed adapter. An empty string is that adapter's default |
| Switch Connection | Queries run on the one you pick |
| Close Connection | Closes one. The last one stays until quit |
| Save Connection | Writes a profile: adapter, connection string, and the loaded keymap names |

The same screen lists the open connections. Enter or Switch selects one. Close
drops the highlighted one.

Plugins are the entry points `files`, `vim`, and `emacs` in the
`harlequin.keymap` group. A config file can still define more keymaps under
new names and name them from a profile. A user keymap may not reuse the name
of an installed plugin.
