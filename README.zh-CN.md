# ccd-profiles

[English](README.md)

使用 ccd-profiles 丝滑地同步多个 profiles/accounts/desktop app 的历史记录。

Claude Desktop 给每个账号、每个组织各存一份 Code 侧边栏。换了公司账号，或者同时开着几个 profile，旧的 session 就留在原来的侧边栏里，虽然对话本身还在磁盘上。ccd-profiles 能列出所有侧边栏，在它们之间复制 session，还能让同时出现在几个侧边栏里的 session 都指向最新的那份对话。

- 只支持 macOS。
- 单个文件，只用 Python 3.9+ 标准库，系统自带的 `python3` 就能跑，不需要安装依赖。
- 输出语言按系统语言设置自动选择英文或中文。

> 本项目与 Anthropic 无关。它读写的是 Claude Desktop 的内部文件，Desktop 升级后可能会变。每次写入前都会先备份。

## 安装

```bash
pipx install git+https://github.com/GoDiao/ccd-profiles
ccd-profiles tui
```

不想安装也可以：

```bash
git clone https://github.com/GoDiao/ccd-profiles
cd ccd-profiles
./ccd_profiles.py tui
```

## 终端界面

```bash
ccd-profiles tui
```

界面分三栏：左边是各个侧边栏（下文叫“空间”），中间是源空间里的 project，右边是高亮 project 下的 session。三栏下面有一行，显示光标所在项目的详情，比如 session 的 ID、目录和最后一条消息的时间。

| 按键 | 作用 |
|---|---|
| Tab / ← → | 切换栏 |
| ↑ ↓ / j k，PgUp PgDn，g G | 移动光标 |
| Enter（左栏） | 把光标所在空间设为源 |
| t（左栏） | 把光标所在空间设为目标 |
| 空格 | 勾选或取消：在中栏是整个 project，在右栏是单个 session |
| a | 切换归档过滤：不含归档、含归档、只看归档 |
| c | 清空勾选 |
| / | 边输入边搜索标题、project、目录和 ID；Enter 保留，Esc 清除 |
| m | 预览把勾选的 session 复制到目标，按 `y` 复制 |
| u（左栏） / S | 选择参与同步的空间 / 预览同步，按 `y` 写入 |
| d | 运行检查（只读），结果在可滚动的页面里显示 |
| b | 列出备份，删除旧备份，删之前会再确认一次 |
| r | 重新读取磁盘上的数据 |
| ? | 帮助 |
| q | 退出；如果写入过，退出后会打印还原命令 |

标记含义：`源` / `目` 是源空间和目标空间，`≡` 是参与同步，`*` 是该 profile 当前登录的账号，`A` 是已归档，`✓` 是已在目标里，`!` 是找不到对话文件。颜色跟随终端主题，设置 `NO_COLOR=1` 可以关掉。窗口至少要 70×12。

## 命令行

```bash
ccd-profiles profiles                       # 列出所有 profile 和空间，带编号，标出各 profile 当前的账号
ccd-profiles ls 2                           # 按 project 分组显示 2 号空间（默认不含归档）
ccd-profiles ls 2 -s --archived include     # 列出每个 session 和它的 8 位 ID
ccd-profiles migrate 2 5 -p my-repo         # 预览把一个 project 从 2 号空间复制到 5 号空间
ccd-profiles migrate 2 5 -s 1a2b3c4d -s 登录页 --apply   # 复制两个 session
ccd-profiles sync 2 5                       # 预览把共有的 session 更新到最新的那份
ccd-profiles doctor                         # 只读检查
ccd-profiles backups --prune                # 每个 profile 只保留最近一份备份
```

空间可以用 `profiles` 里的编号指定，也可以写成 `profile/账号ID前缀/组织ID前缀`，比如 `Claude/1a2b`。只写 profile 名时，指的是它当前登录的账号。

### migrate

- 只复制，不移动：源空间不做任何改动。复制完以后，同一个 session 在两个侧边栏里是两份各自独立的状态。
- 用 `-p <project>`（名字或路径，可写多次）、`--all-projects` 或 `-s <session>`（可写多次）选择要复制的内容；都不写时会交互选择。
- `-s` 先按 ID 前缀匹配（至少 6 位），匹配不到再按标题里的文字匹配。某一项匹配到多个或一个都没有，就列出来，整次都不复制。用 `-s` 时默认包含归档的 session，其他情况用 `--archived exclude|include|only` 控制。
- 跳过目标里已经有的 session，也跳过在目标里删除过的（加 `--force` 可以强制复制）。
- 复制归档的 session 时，会同步更新目标空间的归档索引。

### sync

- 只处理在两个或更多空间里都有的 session，不会把 session 带到原来没有它的空间。各个空间保留自己的归档状态。
- “最新”看的是条目指向的对话文件里最后一条消息的时间，因为在终端里 `claude --resume` 聊天只写对话文件，不会更新任何侧边栏。时间相同时再比较侧边栏的时间。
- 冲突的 session 会跳过并列出来：各空间指向不同的对话文件，而且较旧的那份在分叉（Desktop 回退或 `/clear`）之后还有新消息，或者两份文件互不相关。用 `--take <ID>=<空间>` 指定以哪边为准。

### doctor

只读，只报告情况。检查四项：

1. 侧边栏条目指向的对话文件已经不存在。
2. 同一个 session 同时出现在多个正在运行的 Desktop profile 里。在两边同时打开并发消息会不会出问题，没有验证过。
3. 在终端里接着聊过、侧边栏时间没跟上的 session。不需要修改：在开着的 Desktop 里按 **Cmd+R** 就能看到新消息。
4. 在终端里 `/clear` 之后接着聊，侧边栏还指着旧文件的 session。判断依据是非公开的字段。

### backups

`backups` 列出所有备份；`backups --prune [--keep N]` 确认后删除旧备份，每个 profile 保留最近 N 份（默认 1 份）。

## 安全措施

- `migrate` 和 `sync` 默认只预览，加 `--apply` 才写入；不加 `-y` 时写入前会再确认。
- 目标 profile 的 Desktop 还在运行时，拒绝写入：Desktop 会拿内存里的数据把这些文件写回去，可能悄悄覆盖掉改动。先用 Cmd+Q 退出它。
- 写入前，把要改动的侧边栏复制到 `<profile>/claude-code-sessions.bak-<时间>/`，并打印还原用的 `rsync` 命令。

## Claude Desktop 怎么存侧边栏

- 侧边栏条目：`~/Library/Application Support/<profile>/claude-code-sessions/<account>/<org>/local_<id>.json`。一个 account/org 目录就是一个侧边栏。
- 归档状态：记在 `isArchived` 字段里，同目录的 `archived-sessions.idx` 也记一份。
- 已删除的 session 会留下一个 `deleted_<uuid>` 标记文件。
- 对话内容：在 `~/.claude/projects/<目录>/<id>.jsonl`，所有账号和 profile 共用。所以复制 session 只需要复制那个小的 JSON 条目。
- git worktree（`.claude/worktrees/*`）里的 session 会归到所属的主仓库下。

实测结论（Desktop 和 Claude Code，2026 年 11 月）：

| 操作 | 在 Desktop 里 | 在终端里 |
|---|---|---|
| 接着聊 | 同一份对话文件 | 同一份文件，Desktop 和终端可以交替用 |
| 回退 | 新建文件，只复制回退点之前的消息，并记下分叉时间 | 不换文件，在同一份文件里开分支 |
| `/clear` | 新建空白文件，侧边栏跟着指过去 | 新建空白文件，侧边栏还指着旧文件 |
| 在终端接着聊之后打开 Desktop | — | Desktop 显示的是缓存，按 Cmd+R 刷新后能看到新消息；侧边栏时间不变 |
| 重启 App、切换模型、`/compact` | 不换文件 | — |

## 语言

按顺序检查 `CCD_LANG`、`LC_ALL`、`LC_MESSAGES`、`LANG`，第一个有值的以 `zh` 开头就用中文，否则用英文。比如 `CCD_LANG=zh ccd-profiles tui`。

## Agent skill

[`skills/ccd-profiles/SKILL.md`](skills/ccd-profiles/SKILL.md) 是给 Claude Code 等 agent 用的 skill，写了什么时候用、怎么用，以及写入前必须遵守的规则：先预览，用户同意后才写入，写入前要先退出目标 Desktop。安装方法是把这个目录软链接到 agent 读取 skill 的目录，比如：

```bash
ln -s "$PWD/skills/ccd-profiles" ~/.claude/skills/ccd-profiles
```

装了 `ccd-profiles` 命令时 skill 直接用它，否则从自己往上两级找 `ccd_profiles.py`，所以要用软链接，不要复制。

## 许可证

MIT
