# ccd-profiles

[中文说明](README.zh-CN.md)

Seamlessly sync your Claude Desktop history across profiles, accounts and Desktop apps with ccd-profiles.

Claude Desktop keeps a separate Code sidebar for every account and organization. Switch to a new company account, or run several profiles side by side, and your old sessions stay behind in the old sidebar, even though the conversations themselves are still on disk. ccd-profiles shows every sidebar, copies sessions between them, and keeps sessions that live in several sidebars pointing at the newest conversation.

- macOS only.
- One file, Python 3.9+ standard library only. Runs on the system `python3`; no dependencies.
- English or Chinese output, chosen from your locale.

> Not affiliated with Anthropic. The files it reads and writes are Claude Desktop internals and may change with any Desktop update. Every write is preceded by a backup.

## Install

```bash
pipx install git+https://github.com/GoDiao/ccd-profiles
ccd-profiles tui
```

Or without installing:

```bash
git clone https://github.com/GoDiao/ccd-profiles
cd ccd-profiles
./ccd_profiles.py tui
```

## Terminal UI

```bash
ccd-profiles tui
```

Three panes: sidebars ("spaces") on the left, the projects of the source space in the middle, and the sessions of the highlighted project on the right. A line under the panes describes whatever the cursor is on, for example a session's ID, folder and the time of its last message.

| Key | Action |
|---|---|
| Tab / ← → | Switch pane |
| ↑ ↓ / j k, PgUp PgDn, g G | Move |
| Enter (left pane) | Set the source space |
| t (left pane) | Set the target space |
| Space | Select a whole project (middle) or one session (right) |
| a | Archive filter: without, with, or only archived sessions |
| c | Clear the selection |
| / | Search titles, projects, folders and IDs as you type; Enter keeps it, Esc clears it |
| m | Preview copying the selection to the target; `y` copies |
| u (left pane) / S | Mark spaces for sync / preview the sync; `y` writes |
| d | Health check (read-only), in a scrollable page |
| b | List backups and delete old ones, with a second confirmation |
| r | Reload from disk |
| ? | Help |
| q | Quit; commands to undo any write are printed on exit |

Markers: `S`/`T` source and target, `≡` marked for sync, `*` the profile's current account, `A` archived, `✓` already in the target, `!` transcript not found. Colors follow your terminal theme; set `NO_COLOR=1` to turn them off. The window must be at least 70×12.

## Command line

```bash
ccd-profiles profiles                       # every profile and space, numbered; marks each profile's current account
ccd-profiles ls 2                           # space 2 grouped by project (archived sessions hidden)
ccd-profiles ls 2 -s --archived include     # list every session with its 8-character ID
ccd-profiles migrate 2 5 -p my-repo         # preview copying a project from space 2 to space 5
ccd-profiles migrate 2 5 -s 1a2b3c4d -s "login page" --apply   # copy two sessions
ccd-profiles sync 2 5                       # preview bringing shared sessions up to the newest copy
ccd-profiles doctor                         # read-only health check
ccd-profiles backups --prune                # delete all but the newest backup per profile
```

A space is the number from `profiles`, or `profile/account-prefix/org-prefix` such as `Claude/1a2b`. A bare profile name means its current account.

### migrate

- Copy only: the source is never changed. Afterwards each sidebar keeps its own independent state for the session.
- Choose what to copy with `-p <project>` (name or path, repeatable), `--all-projects`, or `-s <session>` (repeatable). Without any of them it asks interactively.
- `-s` matches an ID prefix of at least 6 hex characters first, then a piece of the title. If an `-s` matches several sessions or none, it lists them and copies nothing. With `-s`, archived sessions are included by default; otherwise use `--archived exclude|include|only`.
- Sessions already in the target are skipped, and so are sessions deleted in the target (`--force` copies those).
- Copying an archived session also updates the target's archive index.

### sync

- Only touches sessions that already exist in two or more of the given spaces; it never adds a session to a space that lacks it. Each space keeps its own archived state.
- "Newest" is decided by the last message in the conversation each entry points at, because chatting in the terminal (`claude --resume`) writes only the conversation and never updates a sidebar. Sidebar time is the tie-break.
- Conflicts are skipped and listed: the entries point at different conversations and the older one received messages after they split (a Desktop rewind or a `/clear`), or the two are unrelated. Resolve one with `--take <ID>=<space>`.

### doctor

Read-only and informational. It reports:

1. Sidebar entries whose conversation file is missing.
2. Sessions present in more than one running Desktop profile. Chatting in the same session from two Desktops at once is untested.
3. Sessions continued in the terminal after their sidebar time. Nothing to fix: press **Cmd+R** in a running Desktop to see the new messages.
4. Entries left on the old conversation after a terminal `/clear`. This relies on an undocumented field.

### backups

`backups` lists them; `backups --prune [--keep N]` deletes all but the newest N per profile (default 1) after a confirmation.

## Safety

- `migrate` and `sync` are dry runs unless `--apply` is given, and ask before writing unless `-y` is given.
- They refuse to write while the target profile's Desktop is running, because Desktop rewrites these files from memory and could silently undo the change. Quit it with Cmd+Q first.
- Before writing, the touched sidebar is copied to `<profile>/claude-code-sessions.bak-<time>/`, and the exact `rsync` command to restore it is printed.

## How Claude Desktop stores the sidebar

- Sidebar entries: `~/Library/Application Support/<profile>/claude-code-sessions/<account>/<org>/local_<id>.json`. One account/org directory is one sidebar.
- Archive state: the `isArchived` field, mirrored in `archived-sessions.idx` in the same directory.
- Deleted sessions leave a `deleted_<uuid>` marker.
- Conversations: `~/.claude/projects/<folder>/<id>.jsonl`, shared by every account and profile. That is why copying a session only needs the small JSON entry.
- Sessions in git worktrees (`.claude/worktrees/*`) are grouped under their main repository.

Observed behavior (Desktop and Claude Code, November 2026):

| Action | In Desktop | In the terminal |
|---|---|---|
| Keep chatting | Same conversation file | Same file; Desktop and terminal can take turns |
| Rewind | New file with the history before the rewind point; the split time is recorded | Same file, branched inside it |
| `/clear` | New empty file; the sidebar follows it | New empty file; the sidebar keeps pointing at the old one |
| Open Desktop after chatting in the terminal | — | Desktop shows a cached view; Cmd+R shows the new messages; the sidebar time does not change |
| Restart, switch model, `/compact` | Same file | — |

## Language

Output is Chinese when `CCD_LANG`, `LC_ALL`, `LC_MESSAGES` or `LANG` (checked in that order) starts with `zh`, English otherwise. For example `CCD_LANG=zh ccd-profiles tui`.

## Agent skill

[`skills/ccd-profiles/SKILL.md`](skills/ccd-profiles/SKILL.md) teaches Claude Code and other agents when and how to use this tool, and the rules for writing: preview first, write only after the user agrees, and quit the target Desktop before writing. Install it by symlinking the directory into your agent's skill folder, for example:

```bash
ln -s "$PWD/skills/ccd-profiles" ~/.claude/skills/ccd-profiles
```

The skill uses the `ccd-profiles` command when it is installed, and otherwise finds `ccd_profiles.py` two levels above itself, so link the directory instead of copying it.

## License

MIT
