---
name: ccd-profiles
description: Inspect and edit the Claude Desktop Code-tab sidebar across Desktop profiles and accounts with the ccd-profiles CLI (macOS). List each profile's sidebar by project, copy sessions or whole projects into another account's sidebar, bring sessions that exist in several sidebars up to the newest copy, run a read-only health check, and prune the tool's backups. Use when the user changed accounts and wants old Code sessions in the new sidebar, wants a session copied to another profile or account, wants sidebars synced, or asks why Desktop does not show messages sent from the terminal.
---

# ccd-profiles

`ccd_profiles.py` is a single-file CLI for the system `python3` (3.9+, standard library only). It works on macOS only.

## Locating the tool

1. If `ccd-profiles` is on `PATH` (installed with pipx), run `ccd-profiles <command> ...`.
2. Otherwise the script sits at the repository root, two levels above this skill: `../../ccd_profiles.py` relative to this file. Resolve symlinks first if the skill directory is a link. Run it as `python3 <path>/ccd_profiles.py <command> ...`.
3. If neither exists, ask the user where the ccd-profiles checkout is. Do not download or install it without asking.

Output is English, or Chinese when the locale starts with `zh`. Prefix commands with `CCD_LANG=en` when you need to parse the output, and show the user results in their language.

## How Desktop stores the sidebar

- One sidebar ("space") is one directory: `~/Library/Application Support/<profile>/claude-code-sessions/<account>/<org>/`. It holds one `local_<id>.json` per session, `archived-sessions.idx`, and `deleted_<uuid>` markers for sessions deleted in that sidebar.
- A sidebar entry points at a transcript in `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`. Transcripts are shared by every account and profile. Copying a session therefore copies only the small JSON entry.
- Continuing a session in the terminal (`claude --resume`) appends to the same transcript but never updates any sidebar entry.
- A terminal `/clear` starts a new transcript, and the sidebar keeps pointing at the old one. A Desktop `/clear` or rewind updates the sidebar itself.
- A running Desktop caches transcripts in its UI. After the user continues a session in the terminal, Desktop shows the new messages only after **Cmd+R** in Desktop. Restarting is not needed.

## Rules

1. **No interactive commands.** Never run `tui`. Never run `migrate` without `-s`, `-p` or `--all-projects`, because it then waits for keyboard input. Commands that ask `[y/N]` must get `-y`, and only under rule 2.
2. **Preview first, write only after a clear yes.** `migrate` and `sync` are dry runs unless `--apply` is given. Show the user the preview, then wait for an explicit yes in chat before running the same command with `--apply -y`. Do the same for `backups --prune` before adding `-y`.
3. **The target Desktop profile must be quit before writing.** The tool refuses to write while that profile's Desktop is running, because Desktop rewrites these files from memory and could silently undo the change. Ask the user to quit it with Cmd+Q. If you are running inside that same Desktop profile, quitting it ends your session; tell the user to run the `--apply` command from a terminal instead, and give them the exact command.
4. **Do not pass `--force`** unless the user asks for it after you explain what it skips: the running-Desktop check, and the check for sessions deleted in the target.
5. **Report the backup.** Every write first backs up the touched sidebars to `<profile>/claude-code-sessions.bak-<time>/`. Relay the backup path and the undo command that the tool prints.

## Commands

| Goal | Command |
|---|---|
| List profiles and sidebars with their numbers and each profile's current account | `profiles` (`--all` also shows empty sidebars) |
| One sidebar grouped by project | `ls <space>` |
| Every session with its 8-character ID | `ls <space> -s [--archived include] [-p <project>]` |
| Copy specific sessions | `migrate <src> <dst> -s <id-prefix or title text> [-s ...]` |
| Copy whole projects | `migrate <src> <dst> -p <project name or path> [-p ...]` or `--all-projects` |
| Bring shared sessions up to the newest copy | `sync <space> <space> [...]` |
| Read-only health check | `doctor` |
| List or prune backups | `backups`, `backups --prune [--keep N]` |

A space is either the number printed by `profiles` or `profile/account-prefix/org-prefix`, for example `Claude/1a2b`. A bare profile name means that profile's current account. Numbers can change when sidebars appear, so run `profiles` again before writing if time has passed.

### Copying (`migrate`)

- Copy only. The source is never changed. Afterwards each sidebar keeps its own independent state for the session.
- `-s` matches an ID prefix of at least 6 hex characters first, then falls back to a case-insensitive substring of the title. If any `-s` matches several sessions or none, the tool lists the candidates and copies nothing. Pick the right ID from that list and ask the user if it is unclear.
- With `-s`, archived sessions are included by default. With `-p`, use `--archived exclude|include|only` (default `exclude`).
- Sessions already in the target are skipped. So are sessions deleted in the target, unless `--force` is given.

### Syncing (`sync`)

- It updates only sessions that already exist in two or more of the given sidebars. It never adds a session to a sidebar that lacks it. Each sidebar keeps its own archived state.
- "Newest" means the latest message in the transcript an entry points at. The sidebar time is used only when two entries point at the same transcript.
- A conflict is reported and skipped when the entries point at different transcripts and the older transcript still received messages after the fork, or when the transcripts are unrelated. Explain the conflict to the user. To resolve one, add `--take <session-id-prefix>=<space>` with the space the user chooses.
- Ask which sidebars should take part. Including a personal account copies work sessions' updates into it as well.

### Health check (`doctor`)

It is read-only and informational. It reports four things:

1. Entries whose transcript is missing.
2. Sessions present in several running profiles. Opening the same session in two Desktops at once has not been tested.
3. Sessions continued in the terminal after their sidebar time. These need nothing beyond Cmd+R in Desktop.
4. Entries left on the old transcript after a terminal `/clear`. This relies on an undocumented `session_id` field. The tool cannot re-point the entry; the user can continue the newer transcript with `claude --resume`.

## Limits

The directory layout and JSON fields are internal to Claude Desktop and may change with any update. If `profiles` finds nothing, or the output looks wrong, stop and tell the user instead of editing files by hand.
