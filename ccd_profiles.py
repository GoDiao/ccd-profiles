#!/usr/bin/env python3
"""ccd-profiles: inspect Claude Desktop profiles and copy or sync Code-tab sessions between accounts.

Desktop keeps the sidebar list of Code sessions per profile, per account, per organization:

    ~/Library/Application Support/<profile>/claude-code-sessions/<account>/<org>/local_<id>.json

The conversation transcripts live in ~/.claude/projects and are shared by every account, so
"migrating" a session means copying its local_<id>.json into another account/org directory.

Runs on the system Python 3.9 with the standard library only. Output is English, or Chinese when
the locale (CCD_LANG, LC_ALL, LC_MESSAGES or LANG) starts with "zh".
"""

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import defaultdict

__version__ = "0.2.0"

APP_SUPPORT = os.environ.get("CCD_APP_SUPPORT") or os.path.expanduser("~/Library/Application Support")
TRANSCRIPTS = os.environ.get("CCD_TRANSCRIPTS") or os.path.expanduser("~/.claude/projects")
HOME = os.path.expanduser("~")
ARCHIVE_IDX = "archived-sessions.idx"


# ---------- messages ----------

def detect_lang():
    for key in ("CCD_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            return "zh" if value.lower().startswith("zh") else "en"
    return "en"


LANG = detect_lang()

M = {
    # general
    "error": ("error: ", "错误："),
    "untitled": ("(untitled)", "（无标题）"),
    "no_cwd": ("(no cwd)", "（无目录）"),
    "cancelled": ("Cancelled.", "已取消。"),
    "arch_exclude": ("without archived", "不含归档"),
    "arch_include": ("with archived", "含归档"),
    "arch_only": ("archived only", "只看归档"),
    "archived_tag": ("  [archived]", "  [已归档]"),
    "more": ("…%d more", "…还有 %d 个"),
    "none": ("none", "无"),
    # spaces
    "no_space": ("no space #%s; run `profiles` to see the numbers", "没有 %s 号空间；运行 `profiles` 查看编号"),
    "ambiguous_space": ("'%s' matches %d spaces; use the number from `profiles` instead",
                        "“%s” 匹配到 %d 个空间；请改用 `profiles` 里的编号"),
    # profiles
    "no_profiles": ("no Claude Desktop profiles found under %s", "在 %s 下没有找到 Claude Desktop 的 profile"),
    "space_line": ("  [%d] account %s  org %s  %3d active %3d archived  last %s%s",
                   "  [%d] 账号 %s  组织 %s  未归档 %3d  已归档 %3d  最后活动 %s%s"),
    "current_mark": ("  <- current account", "  ← 当前账号"),
    "empty_hidden": ("\n(empty spaces hidden; --all to show them)", "\n（没有 session 的空间已隐藏，加 --all 显示）"),
    "running_now": ("\nRunning now (quit the target profile before writing): %s",
                    "\n正在运行（写入前先退出目标 profile）：%s"),
    # ls
    "ls_head": ("%s  (%d sessions, %s)\n", "%s  （%d 个 session，%s）\n"),
    "ls_proj": ("[%2d] %-55s %3d  (%d archived)  last %s", "[%2d] %-55s %3d  （已归档 %d）  最后活动 %s"),
    "ls_legend": ("\nA = archived, ! = transcript not found in ~/.claude/projects",
                  "\nA = 已归档，! = 在 ~/.claude/projects 里找不到对话文件"),
    # session matching
    "nothing_copied": ("Nothing copied:", "没有复制任何 session："),
    "no_match": ("“%s”: no session matches", "“%s”：没有匹配的 session"),
    "many_match": ("“%s”: matches %d sessions; be more specific (ID prefix or a longer title):",
                   "“%s”：匹配到 %d 个，请写得更具体（用 ID 前缀或更长的标题）："),
    # project picker
    "pick_line": ("  [%2d] %-55s %3d  (%d archived, %d already in target)",
                  "  [%2d] %-55s %3d  （已归档 %d，已在目标 %d）"),
    "pick_prompt": ("\nProjects to copy (numbers like 1,3-5, or 'all'): ",
                    "\n要复制的 project（编号，比如 1,3-5，或者 all）："),
    "bad_selection": ("bad selection: %s", "无效的选择：%s"),
    # migrate
    "same_space": ("source and target are the same space", "源和目标是同一个空间"),
    "no_project": ("no project matches %s (%s)", "没有匹配 %s 的 project（%s）"),
    "migrate_head": ("%s -> %s   (%s)\n", "%s → %s   （%s）\n"),
    "to_copy": ("  to copy: %d   (%d archived, %d without transcript)",
                "  将复制：%d   （已归档 %d，找不到对话文件 %d）"),
    "skip_already": ("  skip: %d already in target%s", "  跳过：%d 个已在目标里%s"),
    "v_to_list": (" (-v to list)", "（加 -v 列出）"),
    "skip_deleted": ("  skip: %s  (deleted in target, --force to copy anyway)",
                     "  跳过：%s  （在目标里删除过，加 --force 强制复制）"),
    "dry_run_copy": ("\nDry run. Add --apply to copy.", "\n这是预览，没有写入。加 --apply 才会复制。"),
    "running_refuse": ("Desktop profile '%s' is running; quit it first (Cmd+Q) so it cannot overwrite the change:\n  %s",
                       "Desktop profile「%s」正在运行；先退出它（Cmd+Q），免得它覆盖这次改动：\n  %s"),
    "confirm_copy": ("\nCopy %d sessions? [y/N] ", "\n复制 %d 个 session？[y/N] "),
    "copied": ("\nCopied %d sessions into %s", "\n已复制 %d 个 session 到 %s"),
    "backup_at": ("Backup of the target before the copy: %s", "复制前目标空间的备份：%s"),
    "undo_copy": ("To undo (with Desktop quit):\n  rsync -a --delete '%s/' '%s/'",
                  "还原（先退出 Desktop）：\n  rsync -a --delete '%s/' '%s/'"),
    "delete_backup": ("Delete the backup once the sidebar looks right:\n  rm -rf '%s'",
                      "确认侧边栏没问题后删除备份：\n  rm -rf '%s'"),
    # sync
    "r_fork": ("%s moved to a new transcript at %s (rewind or /clear), but the old one that %s points at "
               "got messages after that (last %s)",
               "%s 在 %s 换了对话文件（回退或 /clear），但 %s 指着的旧文件在那之后还有消息（最后一条 %s）"),
    "r_ahead": ("%s already moved to a new transcript, but the old one that %s points at got newer messages later",
                "%s 已经换到新的对话文件，但 %s 指着的旧文件后来又有了更新的消息"),
    "r_unrelated": ("%s and %s point at unrelated transcripts", "%s 和 %s 指向互不相关的对话文件"),
    "backups_before": ("Backups taken before the change:", "改动前的备份："),
    "undo_line": ("    undo (quit Desktop first): rsync -a --delete '%s/' '%s/'",
                  "    还原（先退出 Desktop）：rsync -a --delete '%s/' '%s/'"),
    "delete_backups_line": ("Delete the backups once the sidebar looks right: rm -rf %s",
                            "确认没问题后删除备份：rm -rf %s"),
    "sync_head": ("Sync %d spaces: %s", "同步 %d 个空间：%s"),
    "in_sync": ("  already in sync: %d", "  已一致：%d"),
    "to_update": ("  to update: %d", "  将更新：%d"),
    "switch_tag": ("  (switches transcript)", "  （换对话文件）"),
    "conflicts_skipped": ("  conflicts, skipped: %d", "  冲突，已跳过：%d"),
    "conflict_copy": ("      %-28s last activity %s  transcript %s%s", "      %-28s 最后活动 %s  对话 %s%s"),
    "newest_tag": ("  <- newest", "  ← 最新"),
    "reason": ("      reason: %s", "      原因：%s"),
    "take_hint": ("  To resolve a conflict add --take <ID>=<space>, for example --take %s=%s",
                  "  处理冲突：加 --take <ID>=<空间> 指定以哪边为准，比如 --take %s=%s"),
    "sync_two": ("sync needs at least two different spaces", "sync 至少需要两个不同的空间"),
    "take_not_in": ("--take %s: %s is not one of the spaces being synced", "--take %s：%s 不在这次同步的空间里"),
    "take_matches": ("--take %s: ID matches %d sessions", "--take %s：ID 匹配到 %d 个 session"),
    "dry_run_sync": ("\nDry run, nothing written. Add --apply to write.", "\n这是预览，没有写入。加 --apply 才会写。"),
    "running_sync": ("Desktop profiles to be written are running; quit them first (Cmd+Q): %s",
                     "要写入的 Desktop profile 还在运行，先退出（Cmd+Q）：%s"),
    "confirm_sync": ("\nUpdate %d sessions? [y/N] ", "\n更新 %d 个 session？[y/N] "),
    "synced": ("\nUpdated %d sessions.", "\n已更新 %d 个 session。"),
    # doctor
    "d1": ("1. Entries whose transcript is missing: %d", "1. 指向的对话文件不存在：%d"),
    "d1_row": ("   %-28s %s  (transcript %s)", "   %-28s %s  （对话 %s）"),
    "d_more": ("   …%d more", "   …还有 %d 个"),
    "d2": ("\n2. Sessions in more than one running profile: %d  (running: %s)",
           "\n2. 同时出现在多个正在运行的 profile 里：%d  （运行中：%s）"),
    "d2_note": ("   Chatting in one of these from two Desktops at once writes to the same transcript (untested). "
                "Most recent:",
                "   在两个 profile 里同时打开同一个 session 并发消息，两边会写同一份对话文件（没有验证过会不会出问题）。"
                "最近活跃的："),
    "d3": ("\n3. Continued in the terminal after the sidebar time: %d", "\n3. 在终端里接着聊过、侧边栏时间没跟上的：%d"),
    "d3_row": ("   %s  terminal last message %s, sidebar time %s  [%s]", "   %s  终端最后消息 %s，侧边栏时间 %s  [%s]"),
    "running_tag": (" (running)", "（运行中）"),
    "d3_note": ("   Same transcript, nothing to fix. In a running Desktop press Cmd+R to see the new messages; "
                "the sidebar time stays as is.",
                "   对话文件是同一份，不用改侧边栏。Desktop 开着的话，按 Cmd+R 刷新后能看到终端里的新消息；侧边栏时间不会变。"),
    "d4": ("\n4. Entries left on the old transcript after a terminal /clear (scanning transcripts…)",
           "\n4. 终端 /clear 之后，侧边栏还指着旧对话文件的 session（正在扫描对话文件…）"),
    "d4_found": ("   found: %d", "   发现：%d 个"),
    "d4_old": ("      sidebar points at %s (last message %s), in: %s", "      侧边栏指着 %s（最后消息 %s），在：%s"),
    "d4_new": ("      newer terminal transcript %s (last message %s)  %s", "      终端里更新的对话文件 %s（最后消息 %s）  %s"),
    "d4_note": ("   Detected through the old session ID recorded in the new file, an undocumented field. "
                "To continue, run claude --resume in a terminal and pick the newer one.",
                "   判断依据是新文件里记录的旧 session ID，这不是公开格式。要接着聊，可以在终端里 claude --resume 选新的那份。"),
    # backups
    "no_backups": ("No backups.", "没有备份。"),
    "del_mark": ("del", "删"),
    "prune_hint": ("\nKeep the newest backup per profile and delete the rest: %s backups --prune (--keep N to keep more)",
                   "\n每个 profile 保留最近 1 份、删除其余：%s backups --prune（--keep N 改保留份数）"),
    "nothing_prune": ("\nNothing to delete (keeping the newest %d per profile).",
                      "\n没有需要删除的（每个 profile 保留最近 %d 份）。"),
    "confirm_prune": ("\nDelete the %d backups marked “%s”, %s in total? [y/N] ",
                      "\n删除 %d 份标“%s”的备份，共 %s？[y/N] "),
    "pruned": ("Deleted %d backups, freed %s.", "已删除 %d 份，释放 %s。"),
    # argparse
    "desc": ("Inspect Claude Desktop profiles and copy or sync Code-tab sessions between accounts.",
             "查看 Claude Desktop 各 profile 的 Code 侧边栏，在账号之间复制或同步 session。"),
    "h_profiles": ("list profiles and their account/org spaces", "列出 profile 和其中的账号/组织空间"),
    "h_all": ("also show spaces with no sessions", "也显示没有 session 的空间"),
    "h_arch": ("archived sessions: exclude (default), include, or only",
               "归档的 session：exclude（默认，不含）、include（含）、only（只看）"),
    "h_ls": ("show one sidebar grouped by project", "按 project 分组显示一个侧边栏"),
    "h_space": ("number from `profiles`, or profile[/account[/org]]", "`profiles` 里的编号，或 profile[/账号[/组织]]"),
    "h_project": ("limit to a project (name or path); repeatable", "只看某个 project（名字或路径），可写多次"),
    "h_sessions": ("list sessions (with IDs) under each project", "列出每个 project 下的 session（带 ID）"),
    "h_migrate": ("copy sessions between spaces (dry run unless --apply)", "在空间之间复制 session（不加 --apply 只预览）"),
    "h_arch_m": ("archived sessions: exclude, include or only; default exclude, or include with --session",
                 "归档的 session：exclude、include 或 only；默认 exclude，用 --session 时默认 include"),
    "h_session": ("session ID prefix or part of its title; repeatable", "session ID 前缀或标题里的一段文字，可写多次"),
    "h_proj_m": ("project name or path; repeatable", "project 名字或路径，可写多次"),
    "h_allp": ("copy every project", "复制所有 project"),
    "h_apply_copy": ("actually copy (default is a dry run)", "真正复制（默认只预览）"),
    "h_yes": ("skip the confirmation prompt", "跳过确认"),
    "h_verbose": ("list skipped sessions", "列出跳过的 session"),
    "h_force": ("copy even if Desktop is running or the target deleted the session",
                "即使 Desktop 在运行、或目标里删除过这个 session，也照样复制"),
    "h_sync": ("bring every copy of shared sessions up to the newest one", "把多个空间共有的 session 都更新到最新的那份"),
    "h_spaces": ("two or more spaces (numbers from `profiles`)", "两个或更多空间（`profiles` 里的编号）"),
    "h_take": ("resolve a conflict: use this space's copy of session ID; repeatable",
               "处理冲突：这个 session 以该空间的那份为准，可写多次"),
    "h_apply_write": ("actually write (default is a dry run)", "真正写入（默认只预览）"),
    "h_doctor": ("read-only check: missing transcripts, shared sessions, terminal activity",
                 "只读检查：缺失的对话文件、共用的 session、终端里的活动"),
    "h_doctor_spaces": ("limit to these spaces (default: all with sessions)", "只检查这些空间（默认：所有有 session 的空间）"),
    "h_backups": ("list backups; --prune deletes old ones", "列出备份；--prune 删除旧备份"),
    "h_prune": ("delete all but the newest --keep per profile", "每个 profile 只保留最近 --keep 份，删除其余"),
    "h_keep": ("backups to keep per profile (default 1)", "每个 profile 保留的份数（默认 1）"),
    "h_tui": ("interactive three-pane browser", "交互式三栏界面"),
    # TUI
    "t_small": ("Window too small; enlarge to at least 70x12", "窗口太小，请放大到至少 70x12"),
    "t_head": ("%s  ->  %s   [%s]   %d selected", "源 %s  →  目标 %s   [%s]   已勾选 %d"),
    "t_unset": ("no source", "未设置"),
    "t_unset_dst": ("no target (t on a space)", "未设置（左栏按 t）"),
    "t_help_key": ("? help", "? 帮助"),
    "p_spaces": ("Spaces", "空间"),
    "p_projects": ("Projects", "Project"),
    "p_sessions": ("Sessions", "Session"),
    "tag_src": ("S", "源"),
    "tag_dst": ("T", "目"),
    "t_pick_src": ("(pick a source in the left pane)", "（先在左栏选一个源）"),
    "t_no_sessions": ("(no sessions match)", "（没有匹配的 session）"),
    "keys": ("Tab panes  Space select  Enter source  t target  m copy  u/S sync  / search  d check  b backups"
             "  ? help  q quit",
             "Tab 换栏  空格 勾选  Enter 设源  t 设目标  m 迁移  u/S 同步  / 搜索  d 检查  b 备份  ? 帮助  q 退出"),
    "legend": ("S/T = source/target   ≡ = in sync set   * = current account   A = archived   ✓ = in target"
               "   ! = no transcript",
               "源/目 = 迁移源/目标   ≡ = 参与同步   * = 当前登录账号   A = 归档   ✓ = 已在目标   ! = 找不到对话记录"),
    "det_session": ("ID %s · %s · last message %s%s", "ID %s · %s · 最后消息 %s%s"),
    "det_terminal": (" (terminal)", "（终端）"),
    "det_no_transcript": ("ID %s · %s · transcript missing", "ID %s · %s · 找不到对话文件"),
    "det_project": ("%s · %d sessions · %d archived · %d in target", "%s · %d 个 session · 已归档 %d · 已在目标 %d"),
    "det_space": ("%s · %d active · %d archived%s%s", "%s · 未归档 %d · 已归档 %d%s%s"),
    "det_current": (" · current account", " · 当前账号"),
    "det_running": (" · Desktop running", " · Desktop 运行中"),
    "dlg_copy": ("Copy", "迁移"),
    "dlg_sync": ("Sync", "同步"),
    "dlg_backups": ("Backups", "备份"),
    "dlg_help": ("Help", "帮助"),
    "t_src_is_dst": ("This space is the target; it cannot also be the source", "这个空间是目标，不能同时当源"),
    "t_src": ("Source: %s", "源：%s"),
    "t_dst_hint": ("Move to the target space in the left pane, then press t", "在左栏移到目标空间上再按 t"),
    "t_dst_same": ("The target cannot be the source", "目标不能和源相同"),
    "t_dst": ("Target: %s", "目标：%s"),
    "t_need_src_dst": ("Set a source (Enter) and a target (t) first", "先设好源（Enter）和目标（t）"),
    "t_need_sel": ("Select projects or sessions with Space first", "先用空格勾选 project 或 session"),
    "t_copy_summary": ("Copy %d (%d archived, %d without transcript)", "复制 %d 个（其中归档 %d，找不到对话记录 %d）"),
    "t_skip_already": ("Skip %d: already in target", "跳过 %d 个：已在目标里"),
    "t_skip_deleted": ("Skip %d: deleted in target (copy them with the CLI and --force)",
                       "跳过 %d 个：在目标里删除过（要复制请用 CLI 加 --force）"),
    "t_nothing_copy": ("Nothing to copy. Press any key", "没有需要复制的。按任意键返回"),
    "t_running_dst": ("Desktop profile “%s” is running.", "目标 profile「%s」的 Desktop 正在运行。"),
    "t_quit_then": ("Quit it (Cmd+Q), then come back and press %s.", "先退出它（Cmd+Q），再回来按 %s。"),
    "any_key": ("Press any key", "按任意键返回"),
    "t_confirm_copy": ("y to copy, any other key to cancel", "按 y 复制，其他键取消"),
    "t_cancelled": ("Cancelled", "已取消"),
    "t_copied": ("Copied %d into %s", "已复制 %d 个到 %s"),
    "t_backup": ("Backup: %s", "备份：%s"),
    "t_undo": ("Undo (quit Desktop first): rsync -a --delete '%s/' '%s/'", "还原（先退出 Desktop）：rsync -a --delete '%s/' '%s/'"),
    "t_rm_backup": ("Delete the backup when it looks right: rm -rf '%s'", "确认没问题后删除备份：rm -rf '%s'"),
    "t_after_hint": ("Press any key (these commands are printed again on exit)", "按任意键返回（退出后会再打印一次这些命令）"),
    "t_copied_msg": ("Copied %d. Open Desktop and check the sidebar", "已复制 %d 个。打开 Desktop 检查侧边栏"),
    "t_need_sync": ("Mark at least two spaces with u in the left pane first", "先在左栏用 u 选至少两个空间"),
    "t_sync_head": ("Sync: %s", "同步：%s"),
    "t_sync_counts": ("In sync %d, to update %d, conflicts skipped %d", "已一致 %d 个，将更新 %d 个，冲突跳过 %d 个"),
    "t_upd_row": ("  update    %s  <- %s", "  更新  %s  ← %s"),
    "t_more": ("  …%d more", "  …还有 %d 个"),
    "t_conf_row": ("  conflict  %s  %s", "  冲突  %s  %s"),
    "t_conf_cli": ("Conflict details and --take are in the CLI:", "冲突的详情和处理（--take）请用 CLI："),
    "t_nothing_sync": ("Nothing to update. Press any key", "没有需要更新的。按任意键返回"),
    "t_running_sync": ("Desktop profiles to be written are running: %s", "要写入的 Desktop profile 还在运行：%s"),
    "t_confirm_sync": ("y to sync, any other key to cancel", "按 y 同步，其他键取消"),
    "t_synced": ("Updated %d sessions", "已更新 %d 个 session"),
    "t_synced_msg": ("Synced %d. Open Desktop and check the sidebar", "已同步 %d 个。打开 Desktop 检查侧边栏"),
    "t_checking": ("Checking…", "正在检查…"),
    "t_check_title": ("Check (read-only)", "检查（只读）"),
    "t_lines": ("%d-%d / %d lines", "%d-%d / %d 行"),
    "t_scroll": ("↑↓ PgUp PgDn scroll  ", "↑↓ / PgUp PgDn 滚动  "),
    "t_back": ("q / Esc back", "q / Esc 返回"),
    "t_backup_rule": ("“%s” marks backups beyond the newest %d per profile.", "标“%s”的是每个 profile 保留最近 %d 份之外的旧备份。"),
    "t_nothing_del": ("Nothing to delete.", "没有需要删除的。"),
    "t_press_y_del": ("Press y to delete these %d, %s in total.", "按 y 删除这 %d 份，共 %s。"),
    "t_y_delete_hint": ("y delete  q / Esc back", "y 删除  q / Esc 返回"),
    "t_no_delete": ("No backups deleted", "没有删除备份"),
    "t_del_confirm": ("Delete %d backups, %s in total. This cannot be undone.", "删除 %d 份备份，共 %s，不能恢复。"),
    "t_y_again": ("Press y again to confirm, any other key to cancel", "再按一次 y 确认，其他键取消"),
    "t_deleted": ("Deleted %d backups, freed %s", "已删除 %d 份备份，释放 %s"),
    "t_u_hint": ("Move to a space in the left pane, then press u", "在左栏移到空间上再按 u"),
    "t_sync_count": ("Spaces to sync: %d", "参与同步的空间：%d 个"),
    "t_arch": ("Archive filter: %s", "归档过滤：%s"),
    "t_refreshed": ("Reloaded", "已刷新"),
    "t_search": ("Search: ", "搜索："),
    "t_search_hint": ("   Enter keep · Esc cancel · matches title, project, path, ID",
                      "   Enter 保留 · Esc 取消 · 匹配标题、project、路径、ID"),
    "t_filter_cleared": ("Search cleared", "已清除搜索"),
    "t_close": ("Any key to close", "按任意键关闭"),
    "help": (
        [("Tab / ← →", "switch pane"),
         ("↑ ↓ j k PgUp PgDn g G", "move"),
         ("Enter (left pane)", "set the source space"),
         ("t (left pane)", "set the target space"),
         ("Space", "select a project (middle) or a session (right)"),
         ("a", "archive filter: without / with / only archived"),
         ("c", "clear the selection"),
         ("/", "search title, project, path or ID; Esc clears it"),
         ("m", "copy the selected sessions to the target"),
         ("u (left pane)", "mark or unmark a space for sync"),
         ("S", "preview the sync of the marked spaces"),
         ("d", "health check (read-only)"),
         ("b", "list backups and delete old ones"),
         ("r", "reload from disk"),
         ("q", "quit")],
        [("Tab / ← →", "切换栏"),
         ("↑ ↓ j k PgUp PgDn g G", "移动光标"),
         ("Enter（左栏）", "把光标所在空间设为源"),
         ("t（左栏）", "把光标所在空间设为目标"),
         ("空格", "勾选 project（中栏）或 session（右栏）"),
         ("a", "归档过滤：不含归档 / 含归档 / 只看归档"),
         ("c", "清空勾选"),
         ("/", "搜索标题、project、路径或 ID；Esc 清除"),
         ("m", "把勾选的 session 复制到目标"),
         ("u（左栏）", "选中或取消参与同步的空间"),
         ("S", "预览选中空间的同步"),
         ("d", "检查（只读）"),
         ("b", "列出备份，删除旧备份"),
         ("r", "重新读取磁盘上的数据"),
         ("q", "退出")]),
}


def _(key, *args):
    en, zh = M[key]
    text = zh if LANG == "zh" else en
    return text % args if args else text


def prog():
    """How the user ran us, for commands printed in hints."""
    name = os.path.basename(sys.argv[0])
    return sys.argv[0] if name.endswith(".py") else "ccd-profiles"


# ---------- discovery ----------

class Space:
    """One account/org directory inside one profile: what one sidebar shows."""

    def __init__(self, profile, account, org, path, current_account):
        self.profile = profile
        self.account = account
        self.org = org
        self.path = path
        self.is_current = account == current_account
        self._sessions = None

    @property
    def ref(self):
        return "%s/%s/%s" % (self.profile, self.account[:8], self.org[:8])

    @property
    def sessions(self):
        if self._sessions is None:
            self._sessions = load_sessions(self.path)
        return self._sessions


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def discover():
    spaces = []
    for root in sorted(glob.glob(os.path.join(APP_SUPPORT, "*", "claude-code-sessions"))):
        pdir = os.path.dirname(root)
        profile = os.path.basename(pdir)
        current = (read_json(os.path.join(pdir, "config.json"), {}) or {}).get("lastKnownAccountUuid")
        for org_path in sorted(glob.glob(os.path.join(root, "*", "*"))):
            if not os.path.isdir(org_path):
                continue
            account = os.path.basename(os.path.dirname(org_path))
            spaces.append(Space(profile, account, os.path.basename(org_path), org_path, current))
    return spaces


def load_sessions(org_path):
    idx = read_json(os.path.join(org_path, ARCHIVE_IDX), {}) or {}
    idx_archived = set(idx.get("archived", []))
    out = []
    for f in glob.glob(os.path.join(org_path, "local_*.json")):
        d = read_json(f)
        if not isinstance(d, dict):
            continue
        sid = d.get("sessionId") or os.path.basename(f)[:-5]
        out.append({
            "file": f,
            "id": sid,
            "cli": d.get("cliSessionId"),
            "title": d.get("title") or _("untitled"),
            "cwd": d.get("cwd") or "",
            "project": project_of(d),
            "archived": bool(d.get("isArchived")) or sid in idx_archived,
            "last": d.get("lastActivityAt") or d.get("createdAt") or 0,
        })
    out.sort(key=lambda s: s["last"], reverse=True)
    return out


def project_of(d):
    """Main repository root, so worktrees roll up into their project."""
    for a in d.get("gitAnchors") or []:
        common = a.get("commonDir")
        if common and os.path.basename(common.rstrip("/")) == ".git":
            return os.path.dirname(common.rstrip("/"))
        if a.get("gitRoot"):
            return a["gitRoot"]
    cwd = d.get("originCwd") or d.get("cwd") or ""
    for marker in ("/.shengjia/.worktree/", "/.claude/worktrees/", "/.shengjia/"):
        if marker in cwd:
            return cwd.split(marker)[0]
    return cwd


def transcript_ids():
    return set(transcript_paths(refresh=True))


# ---------- helpers ----------

def short(path):
    if "/scratch-workspaces/" in path:
        return "scratch:" + os.path.basename(path)
    return "~" + path[len(HOME):] if path.startswith(HOME) else path


def short_id(sid):
    return sid[len("local_"):][:8] if sid.startswith("local_") else sid[:8]


def day(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else "-"


def minute(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M") if ms else "-"


def keep(s, archived):
    return archived == "include" or (archived == "only") == s["archived"]


def arch_label(archived):
    return _("arch_" + archived)


def resolve(spaces, spec):
    """Accept a number from `profiles`, or profile[/account-prefix[/org-prefix]]."""
    if spec.isdigit():
        i = int(spec)
        if 1 <= i <= len(spaces):
            return spaces[i - 1]
        die(_("no_space", spec))
    parts = spec.split("/")
    hits = [s for s in spaces if s.profile.lower() == parts[0].lower()
            and (len(parts) < 2 or s.account.startswith(parts[1]))
            and (len(parts) < 3 or s.org.startswith(parts[2]))]
    if len(parts) < 2:
        hits = [s for s in hits if s.is_current] or hits
    with_sessions = [s for s in hits if s.sessions]
    if len(hits) > 1 and len(with_sessions) == 1:
        hits = with_sessions
    if len(hits) == 1:
        return hits[0]
    die(_("ambiguous_space", spec, len(hits)))


def group_by_project(sessions):
    g = defaultdict(list)
    for s in sessions:
        g[s["project"]].append(s)
    return sorted(g.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def running_desktops():
    """{profile name: process line} for running Desktop main processes."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    except OSError:
        return {}
    found = {}
    for line in out.splitlines():
        if ".app/Contents/MacOS/Claude" not in line or "Helper" in line:
            continue
        profile = "Claude"  # default profile when no --user-data-dir is given
        if "--user-data-dir=" in line:
            profile = os.path.basename(line.split("--user-data-dir=", 1)[1].split(" --")[0].rstrip("/"))
        found[profile] = line.strip()
    return found


def die(msg):
    print(_("error") + msg, file=sys.stderr)
    sys.exit(1)


# ---------- commands ----------

def cmd_profiles(args):
    spaces = discover()
    if not spaces:
        die(_("no_profiles", short(APP_SUPPORT)))
    last_profile = None
    for i, s in enumerate(spaces, 1):
        if s.profile != last_profile:
            print("\n%s  (%s)" % (s.profile, short(os.path.dirname(os.path.dirname(os.path.dirname(s.path))))))
            last_profile = s.profile
        ss = s.sessions
        if not ss and not args.all:
            continue
        active = sum(not x["archived"] for x in ss)
        print(_("space_line", i, s.account[:8], s.org[:8], active, len(ss) - active,
                day(ss[0]["last"]) if ss else "-", _("current_mark") if s.is_current else ""))
    if not args.all:
        print(_("empty_hidden"))
    running = running_desktops()
    if running:
        print(_("running_now", ", ".join(sorted(running))))


def cmd_ls(args):
    space = resolve(discover(), args.space)
    sessions = [s for s in space.sessions if keep(s, args.archived)]
    if args.project:
        sessions = [s for s in sessions if match_project(s["project"], args.project)]
    print(_("ls_head", space.ref, len(sessions), arch_label(args.archived)))
    have = transcript_ids() if args.sessions else None
    for i, (proj, ss) in enumerate(group_by_project(sessions), 1):
        a = sum(x["archived"] for x in ss)
        print(_("ls_proj", i, short(proj) or _("no_cwd"), len(ss), a, day(ss[0]["last"])))
        if args.sessions:
            for x in ss:
                flags = ("A" if x["archived"] else " ") + ("" if x["cli"] in have else "!")
                sub = x["cwd"][len(proj):] if x["cwd"].startswith(proj) else x["cwd"]
                print("      %-2s %s  %s  %s%s" % (flags, day(x["last"]), short_id(x["id"]), x["title"][:70],
                                                  ("   [" + sub.lstrip("/") + "]") if sub else ""))
    if args.sessions:
        print(_("ls_legend"))


HEX_RE = re.compile(r"^(local_)?[0-9a-f-]{6,}$")


def match_sessions(sessions, wanted):
    """Pick sessions by ID prefix or a piece of the title. Exits if one is missing or ambiguous."""
    picked, problems = [], []
    for w in wanted:
        key = w.strip()
        hits = []
        if HEX_RE.match(key.lower()):
            prefix = key.lower()[len("local_"):] if key.lower().startswith("local_") else key.lower()
            hits = [s for s in sessions if s["id"][len("local_"):].startswith(prefix)
                    or s["id"].startswith(prefix)]
        if not hits:
            hits = [s for s in sessions if key.lower() in s["title"].lower()]
        if not hits:
            problems.append(_("no_match", w))
        elif len(hits) > 1:
            problems.append(_("many_match", w, len(hits)))
            problems += ["    %s  %s  %s%s" % (short_id(s["id"]), day(s["last"]), s["title"][:60],
                                               _("archived_tag") if s["archived"] else "") for s in hits[:20]]
            if len(hits) > 20:
                problems.append("    " + _("more", len(hits) - 20))
        elif hits[0] not in picked:
            picked.append(hits[0])
    if problems:
        die("\n".join([_("nothing_copied")] + problems))
    return picked


def match_project(proj, wanted):
    for w in wanted:
        w = os.path.expanduser(w).rstrip("/")
        if proj == w or os.path.basename(proj) == w:
            return True
    return False


def pick_projects(groups, dst):
    for i, (proj, ss) in enumerate(groups, 1):
        a = sum(x["archived"] for x in ss)
        there = sum(os.path.exists(os.path.join(dst.path, os.path.basename(x["file"]))) for x in ss)
        print(_("pick_line", i, short(proj) or _("no_cwd"), len(ss), a, there))
    raw = input(_("pick_prompt")).strip()
    if raw.lower() == "all":
        return [p for p, _x in groups]
    chosen = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        lo, _sep, hi = part.partition("-")
        try:
            for n in range(int(lo), int(hi or lo) + 1):
                chosen.add(groups[n - 1][0])
        except (ValueError, IndexError):
            die(_("bad_selection", part))
    return [p for p, _x in groups if p in chosen]


def plan_copy(selected, dst, force=False):
    """Split selected sessions into (to copy, [(skipped, reason)]); reason is 'already' or 'deleted'."""
    plan, skipped = [], []
    for s in selected:
        name = os.path.basename(s["file"])
        uuid = s["id"][len("local_"):] if s["id"].startswith("local_") else s["id"]
        if os.path.exists(os.path.join(dst.path, name)):
            skipped.append((s, "already"))
        elif os.path.exists(os.path.join(dst.path, "deleted_" + uuid)) and not force:
            skipped.append((s, "deleted"))
        else:
            plan.append(s)
    return plan, skipped


def backup_space(space, stamp):
    """Copy one account/org directory to <profile>/claude-code-sessions.bak-<stamp>/; return the copy."""
    backup = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(space.path))),
                          "claude-code-sessions.bak-" + stamp, space.account, space.org)
    shutil.copytree(space.path, backup)
    return backup


def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def write_json(path, data):
    """Write atomically, in the compact form Desktop itself uses."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def apply_copy(plan, dst):
    """Back up the target space, copy the sessions in, keep the archive index in step.

    Returns the backup directory of the target space.
    """
    backup = backup_space(dst, now_stamp())
    for s in plan:
        shutil.copy2(s["file"], os.path.join(dst.path, os.path.basename(s["file"])))
    archived_ids = [s["id"] for s in plan if s["archived"]]
    if archived_ids:
        idx_path = os.path.join(dst.path, ARCHIVE_IDX)
        idx = read_json(idx_path, None) or {"v": 1, "archived": []}
        idx["archived"] = sorted(set(idx.get("archived", [])) | set(archived_ids))
        write_json(idx_path, idx)
    return backup


# ---------- transcripts ----------
#
# What the sidebar records can be stale: chatting from the terminal (`claude --resume`) appends to
# the same transcript without touching any Desktop sidebar. So "how recent is this session" is read
# from the transcript itself: the timestamp of its newest real (non-meta) user/assistant message.

_TRANSCRIPT_PATHS = None
_LAST_MESSAGE = {}
_LAST_ENTRYPOINT = {}  # cli -> entrypoint of its newest real message (cli / claude-desktop)
UUID_RE = re.compile(rb'"uuid":"([0-9a-f-]{36})"')
SESSION_ID_RE = re.compile(rb'"session_id":"([0-9a-f-]{36})"')


def transcript_paths(refresh=False):
    global _TRANSCRIPT_PATHS
    if _TRANSCRIPT_PATHS is None or refresh:
        _TRANSCRIPT_PATHS = {os.path.basename(p)[:-6]: p
                             for p in glob.glob(os.path.join(TRANSCRIPTS, "*", "*.jsonl"))}
        _LAST_MESSAGE.clear()
        _LAST_ENTRYPOINT.clear()
    return _TRANSCRIPT_PATHS


def iso_ms(ts):
    """'2026-11-06T13:39:00.123Z' -> epoch milliseconds (Python 3.9 fromisoformat is strict)."""
    try:
        ts = ts.replace("Z", "+00:00")
        if "." in ts:
            head, rest = ts.split(".", 1)
            frac = rest[:len(rest) - len(rest.lstrip("0123456789"))]
            ts = head + "." + (frac + "000000")[:6] + rest[len(frac):]
        return int(datetime.datetime.fromisoformat(ts).timestamp() * 1000)
    except (ValueError, AttributeError, TypeError):
        return None


def real_message(e):
    return (isinstance(e, dict) and e.get("type") in ("user", "assistant")
            and not e.get("isMeta") and bool(e.get("timestamp")))


def last_message_ms(cli):
    """Time of the newest real message in a transcript, reading the file from the end."""
    if cli in _LAST_MESSAGE:
        return _LAST_MESSAGE[cli]
    path = transcript_paths().get(cli)
    found = None
    if path:
        with open(path, "rb") as f:
            pos = f.seek(0, os.SEEK_END)
            carry = b""
            while pos > 0 and found is None:
                step = min(1 << 20, pos)
                pos -= step
                f.seek(pos)
                lines = (f.read(step) + carry).split(b"\n")
                carry = lines.pop(0) if pos > 0 else b""
                for raw in reversed(lines):
                    if b'"timestamp"' not in raw:
                        continue
                    try:
                        e = json.loads(raw)
                    except ValueError:
                        continue
                    if real_message(e):
                        found = iso_ms(e["timestamp"])
                        _LAST_ENTRYPOINT[cli] = e.get("entrypoint")
                        break
    _LAST_MESSAGE[cli] = found
    return found


def fork_time(parent, child):
    """When `child` stopped repeating `parent`: the time of its first message not copied from it.

    A Desktop rewind copies the history before the rewind point; /clear copies nothing.
    """
    paths = transcript_paths()
    if child not in paths:
        return None
    parent_ids = set()
    if parent in paths:
        with open(paths[parent], "rb") as f:
            parent_ids = set(UUID_RE.findall(f.read()))
    with open(paths[child], encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if real_message(e) and (e.get("uuid") or "").encode() not in parent_ids:
                return iso_ms(e["timestamp"])
    return None


# ---------- sync ----------
#
# A session copied into several spaces has one local_<id>.json per space. Sync makes every copy
# match the copy whose transcript is the most recent, except the fields each space keeps for itself.
#
# Danger: the copies can fork. A Desktop rewind and /clear move a session to a new transcript file.
# If space A did that while the old file kept receiving messages (from space B, or from the
# terminal), making B follow A would hide those later messages. Such sessions are conflicts.

KEEP_PER_SPACE = ("isArchived",)


def activity(d):
    """Sort key for 'most recent copy': newest transcript message first, sidebar time as tie-break."""
    return (last_message_ms(d.get("cliSessionId")) or 0, d.get("lastActivityAt") or 0)


def plan_sync(spaces, take=None):
    """Compare the copies of every session present in two or more of `spaces`.

    `take` maps session id -> space path to force as the winner (resolves a conflict).
    Returns (updates, conflicts, in_sync):
      updates   [{"id", "title", "winner": (space, data), "targets": [(space, data)]}]
      conflicts [{"id", "title", "winner": space, "copies": [(space, data)], "reasons": [str]}]
      in_sync   [(id, [(space, data)])]
    """
    take = take or {}
    transcript_paths(refresh=True)
    copies_by_id = defaultdict(list)
    for sp in spaces:
        for f in glob.glob(os.path.join(sp.path, "local_*.json")):
            d = read_json(f)
            if isinstance(d, dict):
                copies_by_id[os.path.basename(f)[:-5]].append((sp, d))

    updates, conflicts, in_sync = [], [], []
    for sid, copies in sorted(copies_by_id.items()):
        if len(copies) < 2:
            continue
        key = lambda d: (d.get("cliSessionId"), d.get("lastActivityAt"))
        if len({key(d) for _sp, d in copies}) == 1:
            in_sync.append((sid, copies))
            continue
        forced = take.get(sid)
        if forced:
            w_sp, w = next(c for c in copies if c[0].path == forced)
        else:
            w_sp, w = max(copies, key=lambda c: activity(c[1]))
        w_cli = w.get("cliSessionId")
        chain = (w.get("priorCliSessionIds") or []) + [w_cli]
        edges = {e.get("parent"): e.get("at") for e in w.get("rewindEdges") or []}
        targets, reasons = [], []
        for sp, d in copies:
            if sp is w_sp or key(d) == key(w):
                continue
            o_cli = d.get("cliSessionId")
            if forced or o_cli == w_cli:
                targets.append((sp, d))
                continue
            if o_cli in chain[:-1]:
                left = edges.get(o_cli) or fork_time(o_cli, chain[chain.index(o_cli) + 1])
                last = last_message_ms(o_cli)
                if last is None or (left is not None and last <= left):
                    targets.append((sp, d))
                    continue
                reasons.append(_("r_fork", w_sp.ref, minute(left), sp.ref, minute(last)))
            elif w_cli in (d.get("priorCliSessionIds") or []):
                reasons.append(_("r_ahead", sp.ref, w_sp.ref))
            else:
                reasons.append(_("r_unrelated", w_sp.ref, sp.ref))
        title = w.get("title") or _("untitled")
        if reasons:
            conflicts.append({"id": sid, "title": title, "winner": w_sp, "copies": copies,
                              "reasons": reasons})
        elif targets:
            updates.append({"id": sid, "title": title, "winner": (w_sp, w), "targets": targets})
        else:
            in_sync.append((sid, copies))
    return updates, conflicts, in_sync


def apply_sync(updates):
    """Back up every space that changes, then write the updates. Returns the backup directories."""
    touched = {}
    for u in updates:
        for sp, _d in u["targets"]:
            touched[sp.path] = sp
    stamp = now_stamp()
    backups = [backup_space(sp, stamp) for sp in touched.values()]
    for u in updates:
        _w_sp, w = u["winner"]
        for sp, d in u["targets"]:
            new = dict(w)
            for k in KEEP_PER_SPACE:
                if k in d:
                    new[k] = d[k]
                else:
                    new.pop(k, None)
            write_json(os.path.join(sp.path, u["id"] + ".json"), new)
    return backups


def sync_undo_lines(backups):
    lines = [_("backups_before")]
    for b in backups:
        space_dir = os.path.join(b.split("/claude-code-sessions.bak-")[0], "claude-code-sessions",
                                 *b.split(os.sep)[-2:])
        lines.append("  " + b)
        lines.append(_("undo_line", b, space_dir))
    roots = sorted({os.path.dirname(os.path.dirname(b)) for b in backups})
    lines.append(_("delete_backups_line", " ".join("'%s'" % r for r in roots)))
    return lines


def sync_report(spaces, updates, conflicts, in_sync):
    lines = [_("sync_head", len(spaces), ", ".join(s.ref for s in spaces)),
             _("in_sync", len(in_sync)),
             _("to_update", len(updates))]
    for u in updates:
        w_sp, w = u["winner"]
        switch = any(d.get("cliSessionId") != w.get("cliSessionId") for _sp, d in u["targets"])
        lines.append("    %s  %s -> %s%s" % (u["title"][:40], w_sp.ref,
                                             ", ".join(sp.ref for sp, _d in u["targets"]),
                                             _("switch_tag") if switch else ""))
    lines.append(_("conflicts_skipped", len(conflicts)))
    for c in conflicts:
        lines.append("    %s  %s" % (short_id(c["id"]), c["title"][:50]))
        for sp, d in sorted(c["copies"], key=lambda x: -(x[1].get("lastActivityAt") or 0)):
            lines.append(_("conflict_copy", sp.ref, minute(d.get("lastActivityAt")),
                           (d.get("cliSessionId") or "")[:8], _("newest_tag") if sp is c["winner"] else ""))
        for r in c["reasons"]:
            lines.append(_("reason", r))
    if conflicts:
        lines.append(_("take_hint", short_id(conflicts[0]["id"]), conflicts[0]["winner"].ref))
    return lines


def cmd_migrate(args):
    spaces = discover()
    src, dst = resolve(spaces, args.src), resolve(spaces, args.dst)
    if src.path == dst.path:
        die(_("same_space"))

    if args.archived is None:  # naming a session picks it even when archived
        args.archived = "include" if args.session else "exclude"
    candidates = [s for s in src.sessions if keep(s, args.archived)]
    groups = group_by_project(candidates)
    if args.session:
        projects = None
    elif args.project:
        projects = [p for p, _x in groups if match_project(p, args.project)]
        if not projects:
            die(_("no_project", ", ".join(args.project), arch_label(args.archived)))
    elif args.all_projects:
        projects = [p for p, _x in groups]
    else:
        print(_("migrate_head", src.ref, dst.ref, arch_label(args.archived)))
        projects = pick_projects(groups, dst)
    selected = match_sessions(candidates, args.session) if args.session else \
        [s for s in candidates if s["project"] in projects]

    have = transcript_ids()
    plan, skipped = plan_copy(selected, dst, args.force)

    print("\n%s -> %s" % (src.ref, dst.ref))
    for proj, ss in group_by_project(plan):
        print("  %-60s %3d" % (short(proj), len(ss)))
    print(_("to_copy", len(plan), sum(s["archived"] for s in plan), sum(s["cli"] not in have for s in plan)))
    already = [s for s, why in skipped if why == "already"]
    if already:
        print(_("skip_already", len(already), "" if args.verbose else _("v_to_list")))
        if args.verbose:
            for s in already:
                print("        %s" % s["title"][:70])
    for s, why in skipped:
        if why == "deleted":
            print(_("skip_deleted", s["title"][:60]))

    if not plan:
        return
    if not args.apply:
        print(_("dry_run_copy"))
        return
    running = running_desktops().get(dst.profile)
    if running and not args.force:
        die(_("running_refuse", dst.profile, running))
    if not args.yes and input(_("confirm_copy", len(plan))).strip().lower() != "y":
        print(_("cancelled"))
        return

    backup = apply_copy(plan, dst)

    print(_("copied", len(plan), short(dst.path)))
    print(_("backup_at", short(backup)))
    print(_("undo_copy", backup, dst.path))
    print(_("delete_backup", os.path.dirname(os.path.dirname(backup))))


def cmd_sync(args):
    all_spaces = discover()
    spaces = []
    for spec in args.spaces:
        sp = resolve(all_spaces, spec)
        if sp not in spaces:
            spaces.append(sp)
    if len(spaces) < 2:
        die(_("sync_two"))

    take = {}
    for t in args.take or []:
        sid, _sep, spec = t.partition("=")
        sp = resolve(all_spaces, spec)
        if sp not in spaces:
            die(_("take_not_in", t, sp.ref))
        sid = sid if sid.startswith("local_") else "local_" + sid
        hits = {os.path.basename(f)[:-5] for s in spaces
                for f in glob.glob(os.path.join(s.path, sid + "*.json"))}
        if len(hits) != 1:
            die(_("take_matches", t, len(hits)))
        take[hits.pop()] = sp.path

    updates, conflicts, in_sync = plan_sync(spaces, take)
    print("\n".join(sync_report(spaces, updates, conflicts, in_sync)))
    if not updates:
        return
    if not args.apply:
        print(_("dry_run_sync"))
        return
    touched = {sp.profile for u in updates for sp, _d in u["targets"]}
    running = {p: l for p, l in running_desktops().items() if p in touched}
    if running:
        die(_("running_sync", ", ".join(sorted(running))))
    if not args.yes and input(_("confirm_sync", len(updates))).strip().lower() != "y":
        print(_("cancelled"))
        return
    backups = apply_sync(updates)
    print(_("synced", len(updates)))
    print("\n".join(sync_undo_lines(backups)))


def cmd_doctor(args):
    """Read-only health check across every space that has sessions."""
    spaces = [resolve(discover(), s) for s in args.spaces] if args.spaces else \
        [s for s in discover() if s.sessions]
    paths = transcript_paths(refresh=True)
    entries = []  # (space, sid, data)
    for sp in spaces:
        for f in glob.glob(os.path.join(sp.path, "local_*.json")):
            d = read_json(f)
            if isinstance(d, dict):
                entries.append((sp, os.path.basename(f)[:-5], d))

    # 1. sidebar entries whose transcript is gone
    missing = [(sp, d) for sp, _sid, d in entries if d.get("cliSessionId") not in paths]
    print(_("d1", len(missing)))
    for sp, d in missing[:20]:
        print(_("d1_row", sp.ref, (d.get("title") or "")[:50], (d.get("cliSessionId") or "")[:8]))
    if len(missing) > 20:
        print(_("d_more", len(missing) - 20))

    # 2. the same session in profiles that are running right now
    running = set(running_desktops())
    profiles_of = defaultdict(set)
    newest = {}
    for sp, sid, d in entries:
        if sp.profile in running:
            profiles_of[sid].add(sp.profile)
            newest[sid] = max(newest.get(sid, (0, "")), (d.get("lastActivityAt") or 0, d.get("title") or ""))
    shared = sorted((sid for sid, ps in profiles_of.items() if len(ps) > 1), key=lambda s: -newest[s][0])
    print(_("d2", len(shared), ", ".join(sorted(running)) or _("none")))
    if shared:
        print(_("d2_note"))
        for sid in shared[:5]:
            print("   %s  %s  [%s]" % (minute(newest[sid][0]), newest[sid][1][:50], ", ".join(sorted(profiles_of[sid]))))

    # 3. continued in the terminal in the same transcript: the file moved on, the sidebar did not
    stale = defaultdict(list)  # transcript -> [(space, title, lastActivityAt)]
    for sp, _sid, d in entries:
        cli = d.get("cliSessionId")
        last = last_message_ms(cli)
        if last and last > (d.get("lastActivityAt") or 0) + 60000 and _LAST_ENTRYPOINT.get(cli) == "cli":
            stale[cli].append((sp, d.get("title") or "", d.get("lastActivityAt") or 0))
    print(_("d3", len(stale)))
    order = sorted(stale, key=lambda c: -(last_message_ms(c) or 0))
    for cli in order[:10]:
        rows = stale[cli]
        titles = sorted({t for _sp, t, _a in rows})
        print(_("d3_row", " / ".join(t[:40] for t in titles), minute(last_message_ms(cli)),
                minute(max(a for _sp, _t, a in rows)),
                ", ".join(sorted(sp.ref + (_("running_tag") if sp.profile in running else "")
                                 for sp, _t, _a in rows))))
    if len(order) > 10:
        print(_("d_more", len(order) - 10))
    if stale:
        print(_("d3_note"))

    # 4. terminal /clear: a newer transcript continues one a sidebar entry still points at
    print(_("d4"), flush=True)
    referenced = set()
    pointing = defaultdict(list)  # current transcript -> [(space, title)]
    for sp, _sid, d in entries:
        referenced.update(d.get("priorCliSessionIds") or [])
        referenced.add(d.get("cliSessionId"))
        pointing[d.get("cliSessionId")].append((sp, d.get("title") or ""))
    found = defaultdict(list)  # old transcript -> [newer orphan transcript]
    for tid, path in paths.items():
        if tid in referenced:
            continue
        with open(path, "rb") as f:
            data = f.read()
        for ref in {m.decode() for m in SESSION_ID_RE.findall(data)} - {tid}:
            if ref in pointing and (last_message_ms(tid) or 0) > (last_message_ms(ref) or 0):
                found[ref].append(tid)
    print(_("d4_found", len(found)))
    for old, news in sorted(found.items(), key=lambda kv: -max(last_message_ms(t) or 0 for t in kv[1])):
        titles = sorted({t for _sp, t in pointing[old]})
        print("   %s" % " / ".join(t[:40] for t in titles))
        print(_("d4_old", old[:8], minute(last_message_ms(old)),
                ", ".join(sorted({sp.ref for sp, _t in pointing[old]}))))
        for t in sorted(news, key=lambda t: -(last_message_ms(t) or 0)):
            print(_("d4_new", t[:8], minute(last_message_ms(t)), short(paths[t])))
    if found:
        print(_("d4_note"))


def find_backups():
    out = []
    for root in glob.glob(os.path.join(APP_SUPPORT, "*", "claude-code-sessions.bak-*")):
        if not os.path.isdir(root):
            continue
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _dn, fs in os.walk(root) for f in fs)
        spaces = sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, "*", "*"))
                        if os.path.isdir(p))
        out.append({"path": root, "profile": os.path.basename(os.path.dirname(root)),
                    "mtime": os.path.getmtime(root), "size": size, "spaces": spaces})
    out.sort(key=lambda b: (b["profile"], -b["mtime"]))
    return out


def plan_prune(backups, keep_n):
    """[(backup, delete?)] keeping the newest keep_n per profile."""
    seen = defaultdict(int)
    out = []
    for b in backups:
        seen[b["profile"]] += 1
        out.append((b, seen[b["profile"]] > keep_n))
    return out


def backup_line(b, drop):
    return "%s %-16s %s  %7s  %s" % (
        fit(_("del_mark") if drop else "", 3), b["profile"],
        datetime.datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M"), human(b["size"]),
        ", ".join("%s/%s" % (a[:8], o[:8]) for a, o in (s.split(os.sep) for s in b["spaces"])))


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f%s" % (n, unit) if unit == "B" else "%.1f%s" % (n, unit)
        n /= 1024.0


def cmd_backups(args):
    backups = find_backups()
    if not backups:
        print(_("no_backups"))
        return
    marked = plan_prune(backups, args.keep if args.prune else 10 ** 9)
    for b, drop in marked:
        print(backup_line(b, drop))
    if not args.prune:
        print(_("prune_hint", prog()))
        return
    doomed = [b for b, drop in marked if drop]
    if not doomed:
        print(_("nothing_prune", args.keep))
        return
    total = sum(b["size"] for b in doomed)
    if not args.yes and input(_("confirm_prune", len(doomed), _("del_mark"), human(total))).strip().lower() != "y":
        print(_("cancelled"))
        return
    for b in doomed:
        shutil.rmtree(b["path"])
    print(_("pruned", len(doomed), human(total)))


# ---------- TUI ----------

def char_width(c):
    if unicodedata.combining(c):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def text_width(text):
    return sum(char_width(c) for c in text)


def fit(text, width):
    """Truncate (with …) or pad `text` to exactly `width` terminal columns."""
    if width <= 0:
        return ""
    total = text_width(text)
    limit = width if total <= width else width - 1
    out, used = [], 0
    for c in text:
        cw = char_width(c)
        if used + cw > limit:
            break
        out.append(c)
        used += cw
    if total > width:
        out.append("…")
        used += 1
    return "".join(out) + " " * (width - used)


def wrap(text, width):
    lines, cur, used = [], [], 0
    for c in text:
        cw = char_width(c)
        if used + cw > width:
            lines.append("".join(cur))
            cur, used = [], 0
        cur.append(c)
        used += cw
    lines.append("".join(cur))
    return lines


def visible_spaces():
    """Spaces with sessions, plus the current account of a profile that has none yet."""
    spaces = discover()
    shown = [s for s in spaces if s.sessions]
    for s in spaces:
        if s.is_current and not any(x.account == s.account and x.sessions for x in spaces
                                    if x.profile == s.profile):
            shown.append(s)
    order = {id(s): i for i, s in enumerate(spaces)}
    return sorted(shown, key=lambda s: order[id(s)])


class Tui:
    def __init__(self, scr):
        import curses
        self.c = curses
        self.scr = scr
        self.archived = "exclude"
        self.focus = 0
        self.cur = [0, 0, 0]
        self.off = [0, 0, 0]
        self.page = 10
        self.selected = set()
        self.msg = ""
        self.after = []
        self.src = self.dst = None
        self.sync_paths = set()
        self.query = ""
        self.searching = False
        self.init_colors()
        self.reload()
        if self.spaces:
            self.src = self.spaces[0]

    def init_colors(self):
        c = self.c
        self.A = {"accent": c.A_BOLD, "ok": 0, "warn": 0, "err": c.A_BOLD, "dim": c.A_DIM,
                  "title": c.A_REVERSE | c.A_BOLD, "bar": c.A_REVERSE}
        if os.environ.get("NO_COLOR") or not c.has_colors():
            return
        try:
            c.start_color()
            try:
                c.use_default_colors()
                bg = -1
            except c.error:
                bg = c.COLOR_BLACK
            c.init_pair(1, c.COLOR_CYAN, bg)
            c.init_pair(2, c.COLOR_GREEN, bg)
            c.init_pair(3, c.COLOR_YELLOW, bg)
            c.init_pair(4, c.COLOR_RED, bg)
            c.init_pair(5, c.COLOR_BLACK, c.COLOR_CYAN)
        except c.error:
            return
        p = c.color_pair
        self.A.update(accent=p(1) | c.A_BOLD, ok=p(2), warn=p(3), err=p(4) | c.A_BOLD,
                      title=p(5) | c.A_BOLD)

    # ----- data -----

    def reload(self):
        src = self.src.path if self.src else None
        dst = self.dst.path if self.dst else None
        self.spaces = visible_spaces()
        by_path = {s.path: s for s in self.spaces}
        self.src, self.dst = by_path.get(src), by_path.get(dst)
        self.dst_names = set(os.listdir(self.dst.path)) if self.dst else set()
        self.have = transcript_ids()
        self.running = set(running_desktops())

    def matches(self, s):
        q = self.query.lower()
        return (not q or q in s["title"].lower() or q in s["project"].lower()
                or q in s["cwd"].lower() or q in short_id(s["id"]))

    def sessions(self):
        if not self.src:
            return []
        return [s for s in self.src.sessions if keep(s, self.archived) and self.matches(s)]

    def chosen(self):
        """Selected sessions under the archive filter, including ones the search hides."""
        if not self.src:
            return []
        return [s for s in self.src.sessions if keep(s, self.archived) and s["id"] in self.selected]

    def in_dst(self, s):
        return os.path.basename(s["file"]) in self.dst_names

    # ----- drawing -----

    def put(self, y, x, text, attr=0):
        try:
            self.scr.addstr(y, x, text, attr)
        except self.c.error:
            pass  # writing the bottom-right cell raises; the text is drawn anyway

    def put_segs(self, y, x, segs, w, base=0):
        """Draw [(text, attr)] in exactly w columns, truncating with … and padding."""
        used = 0
        for text, attr in segs:
            room = w - used
            if room <= 0:
                break
            tw = text_width(text)
            if tw > room:
                self.put(y, x + used, fit(text, room), attr | base)
                used = w
                break
            self.put(y, x + used, text, attr | base)
            used += tw
        if used < w:
            self.put(y, x + used, " " * (w - used), base)

    def box(self, y, x, h, w, title, focused):
        from curses.textpad import rectangle
        attr = self.A["accent"] if focused else self.A["dim"]
        self.scr.attron(attr)
        try:
            rectangle(self.scr, y, x, y + h - 1, x + w - 1)
        except self.c.error:
            pass
        self.scr.attroff(attr)
        self.put(y, x + 2, " %s " % title, (self.A["title"] if focused else self.c.A_BOLD))

    def rows(self, pane, items, y, x, h, w):
        n = len(items)
        self.cur[pane] = max(0, min(self.cur[pane], n - 1))
        if self.cur[pane] < self.off[pane]:
            self.off[pane] = self.cur[pane]
        elif self.cur[pane] >= self.off[pane] + h:
            self.off[pane] = self.cur[pane] - h + 1
        self.off[pane] = max(0, min(self.off[pane], max(0, n - h)))
        for i in range(self.off[pane], min(n, self.off[pane] + h)):
            base = 0
            if i == self.cur[pane]:
                base = self.c.A_REVERSE if self.focus == pane else self.c.A_BOLD
            item = items[i]
            segs = [(item, self.A["dim"])] if isinstance(item, str) else item
            self.put_segs(y + i - self.off[pane], x, segs, w, base)
        if n > h:
            self.put(y + h - 1, x + w - 5, "%3d%%" % (100 * (self.cur[pane] + 1) // n), self.A["dim"])

    def detail(self, groups, proj_sessions):
        """One line about whatever the cursor is on."""
        if self.focus == 0 and self.spaces:
            sp = self.spaces[self.cur[0]]
            active = sum(not x["archived"] for x in sp.sessions)
            return _("det_space", sp.ref, active, len(sp.sessions) - active,
                     _("det_current") if sp.is_current else "",
                     _("det_running") if sp.profile in self.running else "")
        if self.focus == 1 and groups:
            proj, ss = groups[self.cur[1]]
            return _("det_project", short(proj) or _("no_cwd"), len(ss), sum(x["archived"] for x in ss),
                     sum(self.in_dst(x) for x in ss))
        if self.focus == 2 and proj_sessions:
            s = proj_sessions[min(self.cur[2], len(proj_sessions) - 1)]
            if s["cli"] not in self.have:
                return _("det_no_transcript", short_id(s["id"]), short(s["cwd"]))
            last = last_message_ms(s["cli"])
            via = _("det_terminal") if _LAST_ENTRYPOINT.get(s["cli"]) == "cli" else ""
            return _("det_session", short_id(s["id"]), short(s["cwd"]), minute(last), via)
        return ""

    def draw(self):
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 12 or w < 70:
            self.put(0, 0, _("t_small"))
            scr.refresh()
            return

        sessions = self.sessions()
        groups = group_by_project(sessions)
        self.cur[1] = max(0, min(self.cur[1], len(groups) - 1))
        proj_sessions = groups[self.cur[1]][1] if groups else []

        # title bar
        head = " ccd-profiles %s   %s" % (__version__, _(
            "t_head", self.src.ref if self.src else _("t_unset"),
            self.dst.ref if self.dst else _("t_unset_dst"), arch_label(self.archived), len(self.chosen())))
        if self.query:
            head += "   /" + self.query
        right = "  " + _("t_help_key") + " "
        self.put(0, 0, fit(head, w - text_width(right)) + right, self.A["title"])

        top, inner = 1, h - 6
        self.page = max(1, inner - 1)
        w0 = max(28, min(36, w // 4))
        w1 = max(30, (w - w0) * 2 // 5)
        w2 = w - w0 - w1

        # pane 0: spaces
        self.box(top, 0, inner + 2, w0, _("p_spaces"), self.focus == 0)
        items = []
        for s in self.spaces:
            if s is self.src:
                tag = (fit(_("tag_src"), 2), self.A["accent"])
            elif s is self.dst:
                tag = (fit(_("tag_dst"), 2), self.A["warn"])
            else:
                tag = ("  ", 0)
            active = sum(not x["archived"] for x in s.sessions)
            right = "%d+%dA" % (active, len(s.sessions) - active)
            name = "%s %s%s" % (s.profile, s.account[:8], "*" if s.is_current else "")
            items.append([tag, ("≡" if s.path in self.sync_paths else " ", self.A["accent"]),
                          (fit(name, w0 - 6 - len(right)), 0), (" " + right, self.A["dim"])])
        self.rows(0, items, top + 1, 1, inner, w0 - 2)

        # pane 1: projects of the source space
        self.box(top, w0, inner + 2, w1, _("p_projects"), self.focus == 1)
        items = []
        for proj, ss in groups:
            n_sel = sum(x["id"] in self.selected for x in ss)
            mark = "[x]" if n_sel == len(ss) else "[-]" if n_sel else "[ ]"
            there = sum(self.in_dst(x) for x in ss)
            count = "%3d " % len(ss)
            tick = ("✓%d" % there) if there else ""
            name = short(proj) or _("no_cwd")
            items.append([(mark + " ", self.A["accent"] if n_sel else 0),
                          (fit(name, w1 - 2 - 4 - 4 - 5), 0), (count, self.A["dim"]),
                          ("%-5s" % tick, self.A["ok"])])
        if not items:
            items = [_("t_pick_src") if not self.src else _("t_no_sessions")]
        self.rows(1, items, top + 1, w0 + 1, inner, w1 - 2)

        # pane 2: sessions of the highlighted project
        proj = groups[self.cur[1]][0] if groups else ""
        self.box(top, w0 + w1, inner + 2, w2, _("p_sessions"), self.focus == 2)
        items = []
        for s in proj_sessions:
            sel = s["id"] in self.selected
            sub = s["cwd"][len(proj):].lstrip("/") if proj and s["cwd"].startswith(proj) else ""
            items.append([("[x] " if sel else "[ ] ", self.A["accent"] if sel else 0),
                          (day(s["last"])[5:] + " ", self.A["dim"]),
                          ("A" if s["archived"] else " ", self.A["warn"]),
                          ("✓" if self.in_dst(s) else " ", self.A["ok"]),
                          (" " if s["cli"] in self.have else "!", self.A["err"]),
                          (" " + s["title"], 0),
                          (("  [" + sub + "]") if sub else "", self.A["dim"])])
        self.rows(2, items, top + 1, w0 + w1 + 1, inner, w2 - 2)

        self.put(h - 3, 0, fit(" " + self.detail(groups, proj_sessions), w), self.A["accent"])
        if self.searching:
            text = " " + _("t_search") + self.query
            self.put(h - 2, 0, fit(text + _("t_search_hint"), w), self.c.A_BOLD)
        else:
            self.put(h - 2, 0, fit(" " + (self.msg or _("legend")), w),
                     self.c.A_BOLD if self.msg else self.A["dim"])
        self.put(h - 1, 0, fit(" " + _("keys"), w), self.A["bar"])
        if self.searching:
            try:
                scr.move(h - 2, min(w - 1, text_width(text)))
            except self.c.error:
                pass
        scr.refresh()

    def dialog(self, lines, prompt, title):
        h, w = self.scr.getmaxyx()
        width = min(w - 4, max(text_width(l) for l in lines + [prompt]) + 4)
        body = [part for l in lines for part in wrap(l, width - 4)]
        body = body[: max(1, h - 6)]
        height = len(body) + 4
        y0, x0 = max(0, (h - height) // 2), max(0, (w - width) // 2)
        for i in range(height):
            self.put(y0 + i, x0, " " * width)
        self.box(y0, x0, height, width, title, True)
        for i, l in enumerate(body):
            self.put(y0 + 1 + i, x0 + 2, l)
        self.put(y0 + height - 2, x0 + 2, fit(prompt, width - 4), self.A["accent"])
        self.scr.refresh()
        return self.scr.get_wch()

    def pager(self, title, lines, hint, exit_keys=()):
        """Full-screen scrollable text. Returns the key that closed it: q / Esc, or one of exit_keys."""
        c = self.c
        top = 0
        while True:
            h, w = self.scr.getmaxyx()
            body = [part for l in lines for part in wrap(l, max(10, w - 2))]
            view = max(1, h - 2)
            top = max(0, min(top, len(body) - view))
            self.scr.erase()
            head = " %s" % title
            if len(body) > view:
                head += "   " + _("t_lines", top + 1, min(len(body), top + view), len(body))
            self.put(0, 0, fit(head, w), self.A["title"])
            for i, l in enumerate(body[top:top + view]):
                self.put(1 + i, 1, l)
            self.put(h - 1, 0, fit(" " + _("t_scroll") + hint, w), self.A["bar"])
            self.scr.refresh()
            k = self.scr.get_wch()
            if k in ("q", "Q", "\x1b") or k in exit_keys:
                return k
            if k in (c.KEY_UP, "k"):
                top -= 1
            elif k in (c.KEY_DOWN, "j"):
                top += 1
            elif k == c.KEY_PPAGE:
                top -= view - 1
            elif k in (c.KEY_NPAGE, " "):
                top += view - 1
            elif k in (c.KEY_HOME, "g"):
                top = 0
            elif k in (c.KEY_END, "G"):
                top = len(body)

    # ----- actions -----

    def move(self, delta):
        sizes = [len(self.spaces), len(group_by_project(self.sessions())), 0]
        if self.focus == 2:
            groups = group_by_project(self.sessions())
            sizes[2] = len(groups[self.cur[1]][1]) if groups else 0
        n = sizes[self.focus]
        self.cur[self.focus] = max(0, min(n - 1, self.cur[self.focus] + delta))
        if self.focus == 1:
            self.cur[2] = self.off[2] = 0

    def set_src(self):
        if not self.spaces:
            return
        sp = self.spaces[self.cur[0]]
        if self.dst is sp:
            self.msg = _("t_src_is_dst")
            return
        if sp is not self.src:
            self.src = sp
            self.selected.clear()
            self.cur[1] = self.cur[2] = self.off[1] = self.off[2] = 0
        self.focus = 1
        self.msg = _("t_src", sp.ref)

    def set_dst(self):
        if self.focus != 0 or not self.spaces:
            self.msg = _("t_dst_hint")
            return
        sp = self.spaces[self.cur[0]]
        if sp is self.src:
            self.msg = _("t_dst_same")
            return
        self.dst = sp
        self.dst_names = set(os.listdir(sp.path))
        self.msg = _("t_dst", sp.ref)

    def toggle(self):
        if self.focus == 0:
            self.set_src()
            return
        groups = group_by_project(self.sessions())
        if not groups:
            return
        ss = groups[self.cur[1]][1]
        if self.focus == 1:
            ids = {s["id"] for s in ss}
            if ids <= self.selected:
                self.selected -= ids
            else:
                self.selected |= ids
        elif ss:
            sid = ss[self.cur[2]]["id"]
            self.selected ^= {sid}
            self.move(1)

    def search(self):
        c = self.c
        previous = self.query
        self.searching = True
        try:
            c.curs_set(1)
        except c.error:
            pass
        while True:
            self.cur[1] = self.cur[2] = self.off[1] = self.off[2] = 0
            self.draw()
            k = self.scr.get_wch()
            if k in ("\n", "\r", c.KEY_ENTER):
                break
            if k == "\x1b":
                self.query = previous
                break
            if k in (c.KEY_BACKSPACE, "\x7f", "\b"):
                self.query = self.query[:-1]
            elif isinstance(k, str) and k.isprintable():
                self.query += k
        self.searching = False
        try:
            c.curs_set(0)
        except c.error:
            pass
        if previous and not self.query:
            self.msg = _("t_filter_cleared")

    def help(self):
        rows = M["help"][1 if LANG == "zh" else 0]
        lines = [fit(k, 24) + d for k, d in rows] + ["", _("legend")]
        self.dialog(lines, _("t_close"), _("dlg_help"))

    def migrate(self):
        if not self.src or not self.dst:
            self.msg = _("t_need_src_dst")
            return
        chosen = self.chosen()
        if not chosen:
            self.msg = _("t_need_sel")
            return
        title = _("dlg_copy")
        plan, skipped = plan_copy(chosen, self.dst)
        already = sum(why == "already" for _s, why in skipped)
        lines = ["%s  ->  %s" % (self.src.ref, self.dst.ref), ""]
        for proj, ss in group_by_project(plan):
            lines.append("  %s  %d" % (fit(short(proj), 40), len(ss)))
        lines.append("")
        lines.append(_("t_copy_summary", len(plan), sum(s["archived"] for s in plan),
                       sum(s["cli"] not in self.have for s in plan)))
        if already:
            lines.append(_("t_skip_already", already))
        if len(skipped) > already:
            lines.append(_("t_skip_deleted", len(skipped) - already))
        if not plan:
            self.dialog(lines, _("t_nothing_copy"), title)
            return
        if running_desktops().get(self.dst.profile):
            self.dialog(lines + ["", _("t_running_dst", self.dst.profile), _("t_quit_then", "m")],
                        _("any_key"), title)
            return
        if self.dialog(lines, _("t_confirm_copy"), title) not in ("y", "Y"):
            self.msg = _("t_cancelled")
            return

        backup = apply_copy(plan, self.dst)
        root = os.path.dirname(os.path.dirname(backup))
        result = [_("t_copied", len(plan), self.dst.ref), _("t_backup", backup),
                  _("t_undo", backup, self.dst.path), _("t_rm_backup", root)]
        self.after += result + [""]
        self.selected.clear()
        self.reload()
        self.dialog(result, _("t_after_hint"), title)
        self.msg = _("t_copied_msg", len(plan))

    def sync(self):
        spaces = [s for s in self.spaces if s.path in self.sync_paths]
        if len(spaces) < 2:
            self.msg = _("t_need_sync")
            return
        title = _("dlg_sync")
        updates, conflicts, in_sync = plan_sync(spaces)
        lines = [_("t_sync_head", ", ".join(s.ref for s in spaces)), "",
                 _("t_sync_counts", len(in_sync), len(updates), len(conflicts)), ""]
        for u in updates[:8]:
            lines.append(_("t_upd_row", u["title"][:40], u["winner"][0].ref))
        if len(updates) > 8:
            lines.append(_("t_more", len(updates) - 8))
        for c in conflicts[:5]:
            lines.append(_("t_conf_row", short_id(c["id"]), c["title"][:40]))
        if conflicts:
            lines += ["", _("t_conf_cli"), "  %s sync %s" % (prog(), " ".join(s.ref for s in spaces))]
        if not updates:
            self.dialog(lines, _("t_nothing_sync"), title)
            return
        touched = {sp.profile for u in updates for sp, _d in u["targets"]}
        running = sorted(p for p in running_desktops() if p in touched)
        if running:
            self.dialog(lines + ["", _("t_running_sync", ", ".join(running)), _("t_quit_then", "S")],
                        _("any_key"), title)
            return
        if self.dialog(lines, _("t_confirm_sync"), title) not in ("y", "Y"):
            self.msg = _("t_cancelled")
            return
        backups = apply_sync(updates)
        result = [_("t_synced", len(updates))] + sync_undo_lines(backups)
        self.after += result + [""]
        self.reload()
        self.dialog(result, _("t_after_hint"), title)
        self.msg = _("t_synced_msg", len(updates))

    def doctor(self):
        import contextlib
        import io
        self.msg = _("t_checking")
        self.draw()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_doctor(argparse.Namespace(spaces=[]))
        self.msg = ""
        self.pager(_("t_check_title"), out.getvalue().rstrip("\n").split("\n"), _("t_back"))

    def backups(self, keep_n=1):
        marked = plan_prune(find_backups(), keep_n)
        doomed = [b for b, drop in marked if drop]
        lines = [backup_line(b, drop) for b, drop in marked] or [_("no_backups")]
        lines += ["", _("t_backup_rule", _("del_mark"), keep_n)]
        title = _("dlg_backups")
        if not doomed:
            self.pager(title, lines + [_("t_nothing_del")], _("t_back"))
            return
        total = sum(b["size"] for b in doomed)
        k = self.pager(title, lines + [_("t_press_y_del", len(doomed), human(total))],
                       _("t_y_delete_hint"), exit_keys=("y", "Y"))
        if k not in ("y", "Y"):
            self.msg = _("t_no_delete")
            return
        if self.dialog([_("t_del_confirm", len(doomed), human(total))], _("t_y_again"), title) not in ("y", "Y"):
            self.msg = _("t_no_delete")
            return
        for b in doomed:
            shutil.rmtree(b["path"])
        self.msg = _("t_deleted", len(doomed), human(total))

    def handle(self, k):
        c = self.c
        self.msg = ""
        if k in ("q", "Q"):
            return False
        if k == "\t":
            self.focus = (self.focus + 1) % 3
        elif k == c.KEY_BTAB:
            self.focus = (self.focus - 1) % 3
        elif k in (c.KEY_RIGHT, "l"):
            self.focus = min(2, self.focus + 1)
        elif k in (c.KEY_LEFT, "h"):
            self.focus = max(0, self.focus - 1)
        elif k in (c.KEY_UP, "k"):
            self.move(-1)
        elif k in (c.KEY_DOWN, "j"):
            self.move(1)
        elif k == c.KEY_PPAGE:
            self.move(-self.page)
        elif k == c.KEY_NPAGE:
            self.move(self.page)
        elif k in (c.KEY_HOME, "g"):
            self.move(-10 ** 6)
        elif k in (c.KEY_END, "G"):
            self.move(10 ** 6)
        elif k in ("\n", "\r", c.KEY_ENTER):
            if self.focus == 0:
                self.set_src()
            else:
                self.focus = min(2, self.focus + 1)
        elif k == "t":
            self.set_dst()
        elif k == " ":
            self.toggle()
        elif k == "a":
            order = ["exclude", "include", "only"]
            self.archived = order[(order.index(self.archived) + 1) % 3]
            self.cur[1] = self.cur[2] = 0
            self.msg = _("t_arch", arch_label(self.archived))
        elif k == "c":
            self.selected.clear()
        elif k == "/":
            self.search()
        elif k == "\x1b" and self.query:
            self.query = ""
            self.msg = _("t_filter_cleared")
        elif k == "?":
            self.help()
        elif k == "m":
            self.migrate()
        elif k == "u":
            if self.focus != 0 or not self.spaces:
                self.msg = _("t_u_hint")
            else:
                self.sync_paths ^= {self.spaces[self.cur[0]].path}
                self.msg = _("t_sync_count", len(self.sync_paths))
        elif k == "S":
            self.sync()
        elif k == "d":
            self.doctor()
        elif k == "b":
            self.backups()
        elif k == "r":
            self.reload()
            self.msg = _("t_refreshed")
        return True

    def run(self):
        try:
            self.c.curs_set(0)
        except self.c.error:
            pass
        self.scr.keypad(True)
        while True:
            self.draw()
            if not self.handle(self.scr.get_wch()):
                return self.after


def cmd_tui(args):
    import curses
    import locale
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    after = curses.wrapper(lambda scr: Tui(scr).run())
    if after:
        print("\n".join(after))


def main():
    p = argparse.ArgumentParser(prog="ccd-profiles", description=_("desc"))
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profiles", help=_("h_profiles"))
    sp.add_argument("--all", action="store_true", help=_("h_all"))
    sp.set_defaults(func=cmd_profiles)

    sp = sub.add_parser("ls", help=_("h_ls"))
    sp.add_argument("space", help=_("h_space"))
    sp.add_argument("--archived", choices=["exclude", "include", "only"], default="exclude", help=_("h_arch"))
    sp.add_argument("-p", "--project", action="append", help=_("h_project"))
    sp.add_argument("-s", "--sessions", action="store_true", help=_("h_sessions"))
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("migrate", help=_("h_migrate"))
    sp.add_argument("src", help=_("h_space"))
    sp.add_argument("dst", help=_("h_space"))
    sp.add_argument("--archived", choices=["exclude", "include", "only"], default=None, help=_("h_arch_m"))
    g = sp.add_mutually_exclusive_group()
    g.add_argument("-s", "--session", action="append", help=_("h_session"))
    g.add_argument("-p", "--project", action="append", help=_("h_proj_m"))
    g.add_argument("--all-projects", action="store_true", help=_("h_allp"))
    sp.add_argument("--apply", action="store_true", help=_("h_apply_copy"))
    sp.add_argument("-y", "--yes", action="store_true", help=_("h_yes"))
    sp.add_argument("-v", "--verbose", action="store_true", help=_("h_verbose"))
    sp.add_argument("--force", action="store_true", help=_("h_force"))
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("sync", help=_("h_sync"))
    sp.add_argument("spaces", nargs="+", help=_("h_spaces"))
    sp.add_argument("--take", action="append", metavar="ID=SPACE", help=_("h_take"))
    sp.add_argument("--apply", action="store_true", help=_("h_apply_write"))
    sp.add_argument("-y", "--yes", action="store_true", help=_("h_yes"))
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("doctor", help=_("h_doctor"))
    sp.add_argument("spaces", nargs="*", help=_("h_doctor_spaces"))
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("backups", help=_("h_backups"))
    sp.add_argument("--prune", action="store_true", help=_("h_prune"))
    sp.add_argument("--keep", type=int, default=1, help=_("h_keep"))
    sp.add_argument("-y", "--yes", action="store_true", help=_("h_yes"))
    sp.set_defaults(func=cmd_backups)

    sp = sub.add_parser("tui", help=_("h_tui"))
    sp.set_defaults(func=cmd_tui)

    args = p.parse_args()
    try:
        args.func(args)
    except (KeyboardInterrupt, EOFError):
        print("\n" + _("cancelled"))
        sys.exit(130)


if __name__ == "__main__":
    main()
