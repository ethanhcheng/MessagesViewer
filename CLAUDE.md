# Messages Viewer Project

## Overview
Build a macOS Messages app GUI clone that reads from a backed-up Messages database folder (on NAS) for archival and viewing purposes. The app should replicate the native macOS Messages interface.

## Data Source
- **Input**: Backup of `~/Library/Messages/` from macOS (contains chat.db + Attachments/)
- **Storage**: NAS (no direct sharing with Claude)
- **Access**: Read-only; never modify source data

## Key Requirements
- [ ] Parse SQLite database (chat.db) with conversations, messages, handles
- [ ] Display conversation list with preview text
- [ ] Show full message thread with proper formatting
- [ ] Handle attachment metadata and display
- [ ] Work with hex-blob encoded messages (Ventura+)
- [ ] Search conversations (if needed)

## Technology Stack
TBD - waiting for your preferences on:
1. Frontend (Web/Electron/Native)
2. Backend (Python/Node.js)
3. Priority features

## References
- [SQLite iMessage Analysis Guide](https://spin.atomicobject.com/search-imessage-sql/)
- [iMessage Reader GitHub](https://github.com/niftycode/imessage_reader)
- [Messages Database Structure](https://davidbieber.com/snippets/2020-05-20-imessage-sql-db/)
