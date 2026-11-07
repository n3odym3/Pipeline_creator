# Text Viewer Module

A versatile terminal log window to inspect runtime text, dictionaries, sample packets, or trigger statuses in real time.

---

## Technical Specifications

- **Inputs**:
  - `TEXT`, `CMD_DICT`, `TRIGGER`, or `ANY`: Accepts strings, dictionaries, or object representations.
- **Outputs**: None.

---

## Features

- **Multi-Format Support**: Automatically parses and pretty-prints incoming dictionaries (`CMD_DICT`) as formatted JSON.
- **Append Mode**: Toggle between overwriting the view on each packet or continuously appending to a running scrollable log.
- **Clipboard Copy**: Direct one-click **Copy** button to copy all viewer content to system clipboard.
- **Quick Clear**: One-click **Clear** button to empty the log buffer.

---

## Usage

Connect any upstream text, command, or data output to inspect stream values in real time in a scrollable, read-only field.
