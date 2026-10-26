# File tree and query tabs

Harlequin's Files tree lists one or more directories. Enter expands a directory,
and copy copies a path. A text file opens into one query tab named for that
file. A query tab labeled `Tab N` is not a file until it is saved into a folder.

neo-tree.nvim (MIT), Fresh (GPL-3.0-or-later, https://github.com/sinelaw/fresh),
Helix (MPL-2.0), and micro (MIT) are inspiration only. No source from those
projects is copied. The behavior here is original Harlequin code under
Harlequin's MIT license.

This note records how those tools connect a file to a buffer, and the slice of
that behavior Harlequin implements.

## How a file becomes a buffer

| | File becomes a buffer | Buffer name | Save | Unsaved state |
| --- | --- | --- | --- | --- |
| neo-tree | A file node opens a Neovim buffer for that path (`open` / `:edit`). A directory node only expands. | The buffer name is the file path. | `:w` writes that buffer to its path. | Neovim's modified flag. |
| Fresh | Opening a file from the explorer creates one editor buffer for that path. | The tab title is the file name. | Save writes the buffer back to its path. | A dirty mark on the tab while the buffer differs from what was loaded or saved. |
| Helix | `:open` or the file picker loads one document for that path. | The document is identified by its path; the status line shows the file name. | `:w` writes that document to its path. | A modified flag while the document differs from disk. |
| micro | Opening a file loads one buffer for that path. | The tab or status title is the file name. | Ctrl-S writes the buffer to its path. | A modified indicator while the buffer is unsaved. |

The same four steps show up in each tool: open the path into one buffer, name
the buffer for the file, mark it when it differs from the last loaded or saved
text, and write it back to that path. Confirming before a dirty buffer is
thrown away is what makes the mark mean something.

## Implemented slice

From the existing Files tree:

- Activating a text file loads its bytes into the query editor, bound to that
  path. One buffer per path: activating it again focuses that buffer.
- Activating a directory still only expands or collapses the tree.
- A non-text or unreadable file does not replace the editor contents.
- The tab shows that file's name. While the buffer differs from the last
  loaded or saved text, the tab shows an unsaved mark. Save writes the buffer
  back to the same path and clears the mark.
- The query editor's open prompt loads a file into the current buffer and
  binds that path. A later save writes the file that was opened.
- Unsaved edits are not discarded without an explicit confirm.
- A blank query buffer can still be created and can still submit SQL.
- Show Files adds a directory by a typed path and leaves directories already
  mounted. Open Folder browses to a directory and adds it the same way. A file
  in that browser is not mounted and does not change the query editor. Close
  Folder removes the directory under the cursor. Closing a directory does not
  discard query buffers.
- Open File browses to a text file and loads one query buffer for that path.
  A directory in that browser only expands or collapses. A non-text file does
  not replace the editor text.
- Open Folder and Open File show a name and the full path, in the same
  dialog as save. New File and New Folder create that name in the folder on
  screen. Rename changes the cursor entry's name. Delete asks with the full
  path, then removes the file, or the folder and what is inside it. The Files
  tab offers the same four commands for the folder under the cursor. One
  directory level is listed. An open query keeps its text: rename follows the
  file, and delete leaves the text with no path.
- Save on a tab that is not a file browses to a folder and asks for a file
  name. The folder does not have to be one already shown. Up lists the parent
  folder, and only the folder on screen is listed. The tab then shows that
  file's name, the full path is on the line under the tabs, and a later save
  writes that file. A tab with no path reads "Not a file". One buffer per
  path. Saving onto an existing file asks first. Type a path is still there.
- Quit stores the path in cache version 2. A version-1 cache, which has
  selection and text only, still loads, and those buffers stay not a file.
  The set of mounted folders is not written to the config profile.
- Typing while a one-level listing is focused narrows that directory to names
  that contain the text. Folders match. The match is case-insensitive and
  literal, so a bracket is a character. Clearing the search shows that
  directory again. Up, or opening a folder, shows the new directory
  unfiltered. The name field edits a name and does not filter the list.
- Each visible query tab has a close control. Close Buffer and `ctrl+w` close
  the same tab. A tab whose text matches the last loaded or saved file closes
  with no prompt and leaves the file bytes. Unsaved text, including a query
  that is not a file and a file wiped to whitespace, asks to Save, close
  without saving, or Cancel. Save writes the editor bytes and then closes the
  tab. A query that is not a file uses the folder-and-name save, and leaving
  that without saving keeps the tab. Cancel keeps the tab. The last tab
  becomes an empty query that is not a file. The path is shown as literal
  text. Close All still asks once before discarding every buffer.

## Deferred

These stay deferred. They are the rest of each product, and they are not part
of this change:

- neo-tree: git decorations, fuzzy find, preview-without-open,
  drag-and-drop, and move.
- Fresh: LSP, diagnostics, AI, and multi-cursor editing.
- Helix: modal editing (the modal keymap) and multi-cursor selections.
- micro's broader editor surface, and turning Harlequin into a general-purpose
  editor.
- Reloading a file when it changes on disk outside the app, editing S3
  objects, and project-wide search.
