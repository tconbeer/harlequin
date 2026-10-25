# File tree and query tabs

Harlequin's Files tree is a directory listing. Enter expands a directory, and
copy copies a path. Query tabs are anonymous buffers labeled `Tab N`. Nothing
loads a file into a tab, and nothing writes a tab back to a file.

This note records how four terminal file/editor tools connect a file to a
buffer, and the small slice of that behavior Harlequin implements.

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
- Unsaved edits are not discarded without an explicit confirm.
- A blank query buffer can still be created and can still submit SQL.

Anonymous buffers keep today's save prompt. Paths are remembered for the
session only; the on-disk buffer cache is unchanged.

## Deferred

These are the rest of each product, and they are not part of this change:

- neo-tree: git decorations, filters, fuzzy find, preview-without-open,
  drag-and-drop, and create/rename/delete/move.
- Fresh: LSP, diagnostics, AI, and multi-cursor editing.
- Helix: modal editing (the modal keymap) and multi-cursor selections.
- micro's broader editor surface, and turning Harlequin into a general-purpose
  editor.
- Reloading a file when it changes on disk outside the app, editing S3
  objects, and project-wide search.
