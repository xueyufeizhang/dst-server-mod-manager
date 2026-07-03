# DST Server Mod Manager

一个部署在 Linux VPS 上的 **Don't Starve Together 专用服务器 Web Mod 管理面板**。

它扫描服务器上已下载的 Workshop mods，解析每个 mod 的 `modinfo.lua`，读取
Master / Caves 的 `modoverrides.lua`，让你在网页上勾选启用/禁用 mod、用表单修改
mod 参数，保存时自动备份并重新生成 `modoverrides.lua`。

技术栈：Python + FastAPI + Jinja2（无前端构建系统），Lua 解析通过调用系统 Lua解释器子进程完成（不依赖脆弱的正则）。

## 功能

- 扫描 `mods_path` 下的 `workshop-<id>` 目录（也兼容 `ugc_mods` 布局的纯数字目录名）
- **按 Workshop ID / URL 添加新 mod**：自动写入 `dedicated_server_mods_setup.lua`
  （服务器下次启动时自动下载）并在选定 shard 的 `modoverrides.lua` 中启用；
  未下载完成的 mod 显示在 “Pending download” 区域，可切换启用/移除
- **直接下载（可选）**：装有 steamcmd 时，可 “Add &amp; download now” 立刻下载 mod，
  马上调参数、只重启一次服务器就全部生效
- **一键删除**：已下载 mod 的 Delete 按钮同时清理本地文件夹、各 shard 的
  modoverrides 条目和下载列表行；配置先备份，误删可从 Backups 恢复（文件可重新下载）
- **Files 页面**：直接查看/编辑 Master、Caves 的 `modoverrides.lua` 和
  `dedicated_server_mods_setup.lua` 原文；保存前 Lua 校验（modoverrides 必须能
  返回 table，setup 文件做编译检查），先备份、原子写入，并有并发冲突检测
  （文件在你打开页面后被别处修改会拒绝保存）
- **Server 页面**：Start / Stop / Restart / Check status 四个控制按钮（对应
  config.yaml 中的四条命令，显示 stdout/stderr）；只读展示集群配置
  （`cluster.ini` 的服务器名称/模式/人数/PvP 等摘要 + 各 shard `server.ini`
  端口、token 是否就位；密码等敏感值自动打码）；主机状态（内存/磁盘用量条、
  负载、运行时长）；以及各 shard `server_log.txt` 的日志查看器（尾部 N 行）
- **一键恢复默认参数**：每个 mod 的配置面板有 “Reset to defaults” 按钮，把所有
  选项恢复为 modinfo 默认值（仅填回表单，点 Save 才真正写入）
- 沙箱环境执行 `modinfo.lua`，提取名称 / 作者 / 版本 / 描述 / `configuration_options`
  （提供 `ChooseTranslationTable`、`locale`、`folder_name` 等 DST 引擎全局变量的桩）
- 单个 mod 解析失败不影响整体，UI 中显示错误信息，仍可启用/禁用
- 读取每个 shard 的 `modoverrides.lua`；文件不存在视为空配置
- **统一配置模式（默认）**：每个 mod 只有一个启用开关和一列参数，保存时把相同
  设置写入所有 shard —— 因为两个 shard 几乎总是跑同一套 mod。检测到两边当前
  不一致时显示 “shards differ” 徽章，保存一次即重新同步。需要分开配置时把
  `dst.unified_mod_config` 设为 `false`，恢复 per-shard 双列模式
- 配置项按 `options` 渲染下拉框，布尔渲染 true/false 下拉框，无预设选项渲染文本框
- 当前值优先取自对应 shard 的 `modoverrides.lua`，否则用 modinfo 的 `default`
- 每次操作前自动备份将被覆盖的文件，按操作分组为一条备份记录（详情页可看
  diff、单独/全部恢复、删除），按 `keep_last` 自动清理；写入采用
  “临时文件 + rename” 原子策略
- `modoverrides.lua` 中存在但 modinfo 里没有的键会**原样保留**，不会被静默丢弃
- 若某 shard 的 `modoverrides.lua` 本身解析失败，**拒绝保存该 shard**，防止覆盖坏文件
- Backups 页面可一键恢复任意备份（恢复前同样先备份当前文件）
- Dashboard 显示路径 / shard 状态 / mod 数量 / 各 shard 启用数量 / 备份数量，
  以及可选的 Restart Server / Check status 按钮（显示 stdout/stderr）
- 默认仅监听 `127.0.0.1` + HTTP Basic Auth，推荐 SSH 隧道访问

## 目录结构

```
dst-server-mod-manager/
├── app/
│   ├── main.py                 # FastAPI 路由 / 鉴权 / 页面
│   ├── config.py               # YAML 配置加载
│   ├── models.py               # 数据模型
│   ├── viewmodels.py           # 模板视图模型 + 表单解码
│   ├── services/
│   │   ├── lua_runner.py       # 调用 Lua 子进程的封装
│   │   ├── mod_scanner.py      # 扫描 mods 目录（带缓存）
│   │   ├── modinfo_parser.py   # modinfo.lua -> Python
│   │   ├── overrides_parser.py # modoverrides.lua -> Python
│   │   ├── overrides_writer.py # Python -> modoverrides.lua（原子写入）
│   │   ├── mod_setup.py        # dedicated_server_mods_setup.lua 管理
│   │   ├── backup.py           # 备份/恢复/清理
│   │   └── server_control.py   # restart/status 命令执行
│   ├── templates/              # Jinja2 模板
│   └── static/                 # CSS + 少量 JS
├── scripts/
│   ├── parse_modinfo.lua       # 沙箱执行 modinfo.lua，输出 JSON
│   └── parse_lua_table.lua     # dofile 风格读取 return {...}，输出 JSON
├── sample_data/                # 本地测试用假数据（不需要真实 DST 服务器）
├── config.example.yaml         # 生产配置模板
├── config.sample.yaml          # 指向 sample_data 的测试配置
├── requirements.txt
└── run.sh
```

## 环境要求

- Linux（macOS 开发也可以）
- Python ≥ 3.9
- 任意一个 Lua 解释器（5.1 ~ 5.5 或 LuaJIT 均可）：

```bash
# Debian / Ubuntu
sudo apt install lua5.4
# 或者
sudo apt install lua5.3
```

面板会按 `lua5.4 → lua5.3 → lua5.2 → lua5.1 → lua → luajit` 的顺序自动探测，
也可以在 `config.yaml` 的 `lua.command` 里手动指定。

## 快速开始（本地用 sample_data 测试）

```bash
git clone <this-repo> && cd dst-server-mod-manager
./run.sh config.sample.yaml     # 首次运行会自动创建 .venv 并安装依赖
```

浏览器打开 <http://127.0.0.1:8080>，用户名 `admin`，密码 `changeme`。

sample 数据包含：

- `workshop-378160973`：完整配置项（布尔 / 数字 / 字符串 / 分节标题 / 自由文本）
- `workshop-123456789`：使用 `ChooseTranslationTable` 的多语言 modinfo
- `workshop-999999999`：故意写坏的 modinfo，演示解析失败时的 UI
- Master 的 `modoverrides.lua` 里有一个 modinfo 中不存在的 `LEGACY_OPTION`，演示“未知键保存时原样保留”

随便改几个选项点 **Save all changes**，然后看
`sample_data/cluster/MyDediServer/*/modoverrides.lua` 和 `backups/` 目录的变化。

## 部署到真实 VPS

### 1. 安装

```bash
sudo apt install git python3-venv lua5.4
# 放在运行 DST 的用户（通常是 steam）家目录下最省事
cd ~ && git clone https://github.com/xueyufeizhang/dst-server-mod-manager.git
cd dst-server-mod-manager
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> 没有 GitHub 访问权限的机器也可以用 tarball：本地
> `git archive -o dst-mod-manager.tar.gz HEAD`，scp 到服务器解压 —— 但后续更新
> 就要手动重复这个过程，能用 git 尽量用 git。

### 2. 配置

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，最重要的两个路径：

```yaml
dst:
  # 包含 Master/、Caves/ 的存档目录
  cluster_path: /home/steam/.klei/DoNotStarveTogether/MyDediServer
  # 已下载 workshop mods 的目录
  mods_path: "/home/steam/Steam/steamapps/common/Don't Starve Together Dedicated Server/mods"
  shards: [Master, Caves]
```

> 如果你的服务器用 `-ugc_directory` 启动，mods 通常在
> `<ugc_dir>/content/322330/` 下（目录名是纯数字 id），把 `mods_path` 指向那里即可。

可选项 `dst.mods_setup_path` 指定 `dedicated_server_mods_setup.lua` 的位置
（“Add mod” 功能会写这个文件）；留空则默认为
`<mods_path>/dedicated_server_mods_setup.lua`，即标准位置。用 `ugc_mods` 布局时需要手动把它指向服务器安装目录下的 `mods/dedicated_server_mods_setup.lua`。

**务必修改 `security.password`。** 相对路径相对于 `config.yaml` 所在目录解析。

运行面板的用户需要对 `Master/`、`Caves/` 目录有写权限（通常直接用运行 DST
的 steam 用户跑面板最简单）。

### 3. 启动

```bash
./run.sh                 # 使用 ./config.yaml
# 或
./run.sh /path/to/config.yaml
# 或不用脚本：
DST_MOD_MANAGER_CONFIG=config.yaml .venv/bin/python -m app.main
```

### 4. systemd 管理面板

`/etc/systemd/system/dst-mod-manager.service`：

```ini
[Unit]
Description=DST Web Mod Manager
After=network.target

[Service]
Type=simple
User=steam
WorkingDirectory=/opt/dst-mod-manager
Environment=DST_MOD_MANAGER_CONFIG=/opt/dst-mod-manager/config.yaml
ExecStart=/opt/dst-mod-manager/.venv/bin/python -m app.main
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dst-mod-manager
```

### 5. 后续更新

代码更新走 git，**你的 `config.yaml` 和 `backups/` 都在 `.gitignore` 里，
更新不会碰它们**。仓库自带一键更新脚本：

```bash
cd ~/dst-server-mod-manager && ./update.sh
```

它会依次执行：`git pull --ff-only` → 同步 pip 依赖 → `sudo systemctl restart
dst-mod-manager`（unit 名可用环境变量 `DST_MOD_MANAGER_SERVICE` 覆盖；没有配
systemd 时会提示手动重启）。日常更新流程就是：本地改完 push，服务器上跑一次
`./update.sh`。

### 6. 通过 SSH 隧道访问（推荐）

面板默认只监听 `127.0.0.1`，**不要**改成 `0.0.0.0` 直接暴露公网。在本地电脑上：

```bash
ssh -L 8080:127.0.0.1:8080 user@your-vps-ip
```

然后浏览器打开 <http://127.0.0.1:8080>。

## 配置服务器控制命令（start / stop / restart / status）

推荐用 systemd 管理 DST 服务器本体，每个 shard 一个 unit（如 `dst-master`、
`dst-caves`）。然后在 `config.yaml` 里配置四个按钮对应的命令：

```yaml
server:
  start_command:   "sudo systemctl start dst-master dst-caves"
  stop_command:    "sudo systemctl stop dst-master dst-caves"
  restart_command: "sudo systemctl restart dst-master dst-caves"
  status_command:  "systemctl status dst-master dst-caves --no-pager -l"
```

通过 sudoers 只授权这几条**完全一致**的命令（`sudo visudo -f /etc/sudoers.d/dst-mod-manager`；
sudoers 按整条命令+参数精确匹配，所以必须与 config.yaml 中的写法逐字相同）：

```
steam ALL=(root) NOPASSWD: /usr/bin/systemctl start dst-master dst-caves
steam ALL=(root) NOPASSWD: /usr/bin/systemctl stop dst-master dst-caves
steam ALL=(root) NOPASSWD: /usr/bin/systemctl restart dst-master dst-caves
```

（`status` 通常不需要 sudo。）这样即使面板被攻破，攻击者也只能启停 DST，而不能
以 root 执行任意命令。面板**不会**默认内置任何危险命令；某条命令留空则对应
按钮不显示。Stop / Restart 有二次确认，执行结果的 stdout/stderr 显示在页面上。

## 查看服务器日志

Server 页面的 Logs 表格列出每个 shard 的 `server_log.txt`（大小、修改时间），
点 view log 查看**日志尾部**（默认最后 200 行，可切 500 / 1000 / 2000，自动滚到
最新一行，Refresh 重新读取）。日志由 DST 服务器进程直接写在 cluster 目录里，
面板以同一用户读取，无需 journalctl 权限。

## 添加新 mod（按 Workshop ID）

Mods 页面顶部的 **Add a mod by Workshop ID**：粘贴 Workshop ID（如 `378160973`）或整个创意工坊 URL（`https://steamcommunity.com/sharedfiles/filedetails/?id=...`），
勾选要启用的 shard，点击 Add mod。面板会：

1. 向 `dedicated_server_mods_setup.lua` 追加 `ServerModSetup("<id>")`
   （已存在则跳过；文件中的手写内容和注释原样保留）；
2. 在选定 shard 的 `modoverrides.lua` 中写入 `enabled = true`（照常先备份）。

mod 文件要等 **DST 服务器下次重启时由服务器自己下载**，下载完成前它显示在
“Pending download” 区域——可以照常勾选/取消各 shard 的启用状态，或点 Remove把误加的条目从 modoverrides 和 setup 文件中删掉（已下载到磁盘的 mod 不允许Remove，只能取消勾选，避免误删配置）。下载完成后刷新页面即可看到名称和全部配置项。

### 直接下载（steamcmd，可选）

装了 steamcmd 的机器上（DST 服务器通常本来就有），添加 mod 时可以选
**Add &amp; download now**：面板用 `steamcmd +login anonymous +workshop_download_item
322330 <id>` 立刻把 mod 下载进 `mods_path`，你马上就能在页面上调参数，然后
**只重启一次** 服务器即可让 mod 和参数同时生效——不必先重启下载、改参数、再重启。

- **Add only** 则保持传统流程：由 DST 服务器在下次重启时自己下载
- Pending download 里的 mod 也有 **Download now** 按钮，可以随时补下载
- 对已下载的 mod 再次执行下载会覆盖旧文件，等于手动更新
- 新版 Workshop 上传的 mod 经 steamcmd 下载后是一个 `*_legacy.bin`（实为 zip 包，
  游戏自带下载器会自行解压）——面板会自动识别并解压出 `modinfo.lua` 等文件
- 下载在**后台执行**：页面顶部显示进度面板（动画进度条 + 已耗时 + steamcmd
  实时输出），完成后自动刷新显示结果；同一时间只允许一个下载任务
- steamcmd 首次运行会自我更新，可能耗时几分钟（`steamcmd.timeout` 默认 900 秒）；
  未安装 steamcmd 时相关按钮自动禁用，其他功能不受影响
- 安装：`sudo apt install steamcmd`（Debian 需启用 non-free；或参考 Valve 官方文档），
  也可在 `config.yaml` 的 `steamcmd.command` 里指定完整路径

Mods 页面同时读取 modoverrides 和下载列表两边的状态，未下载的 mod 按语义分两个区域：

- **Pending download**：在 `dedicated_server_mods_setup.lua` 里、等待服务器重启后
  下载的 mod（包括只在下载列表、还没在任何 shard 启用的——勾选后保存即可）
- **Orphaned entries — won't be downloaded**：只被 modoverrides 引用、不在下载列表
  里的残留条目，服务器**不会**下载它。常见于只恢复了 ModSetup 备份、或手动删了
  setup 行的情况；点条目上的 “Re-add to download list” 一键加回下载列表
  （shard 启用状态和参数原样保留），或 Remove 清掉残留

已下载但不在下载列表的 mod 也会带 “not in download list” 黄色徽章，提示服务器
不会重新下载/更新它。

## 恢复默认参数

每个 mod 的 Configuration 面板里有 **Reset to defaults** 按钮：把该 mod 所有
shard 的全部选项填回 modinfo.lua 中的默认值。这只是回填表单，检查无误后点
**Save all changes** 才会真正写入文件。

## 备份

备份按**操作**分组：一次保存 / 添加 mod / 移除 mod / 恢复 = 一条备份记录，
无论这次操作覆盖了几个文件（Master、Caves、`dedicated_server_mods_setup.lua` 的副本打包放在同一个目录里）：

```
backups/
└── 20260702-104512/            # 一条记录 = 一次操作
    ├── meta.json               # 时间、操作类型（save / add workshop-x / ...）
    ├── Master__modoverrides.lua
    └── Caves__modoverrides.lua
```

- Backups 页面按时间列出记录；点进详情页可以看到**该次操作做了哪些更改**（备份内容 vs 下一条备份或当前文件的 diff），并可展开查看备份文件原文
- 详情页操作：单独 Restore Master / Restore Caves、一键 Restore all、
  Delete 删除该条备份
- 列表页支持批量删除：勾选多条记录（表头可全选）后点 Delete selected
- 恢复前会先把当前文件再备份一次（记录为 `before restoring backup ...`）
- 原文件不存在（第一次保存）则跳过备份，不产生空记录
- **只备份真正变化的文件**：保存时逐 shard 对比生成内容和磁盘内容，没变的
  文件不写入也不备份；如果整次操作什么都没改，则不产生任何备份记录，
  提示 "No changes detected"。恢复时同理，与备份内容一致的文件会被跳过
- Mods 页面的 Save 按钮在没有任何改动时置灰，有改动才可点击（纯前端体验，
  后端独立做同样的判断）
- 只保留最近 `backup.keep_last` 条记录（默认 20，设为 0 表示不清理）
- 生成的 `modoverrides.lua` 内容是确定性的（无时间戳），因此 diff 只显示真实的配置变化

## 常见问题（FAQ）

**为什么修改后需要重启 DST server？**
DST 专用服务器只在启动时读取一次 `modoverrides.lua`，运行中修改文件不会热加载，所以保存后必须重启（或用重启按钮）才能生效。

**为什么某些 mod 参数解析不出来？**
`modinfo.lua` 是一段会被执行的 Lua 代码。本面板在沙箱里执行它并提供了常见的DST 全局变量桩（`ChooseTranslationTable`、`locale`、`folder_name`），但个别 mod会引用更冷门的引擎函数或有语法错误，这时该 mod 会标记为 “parse failed”，错误原因显示在卡片上——仍然可以启用/禁用它，已有配置会原样保留。

**为什么不要把面板直接暴露到公网？**
这个面板可以改服务器文件、还可能配置了重启命令，而它只有一层 Basic Auth（明文HTTP）。公网暴露意味着密码可被嗅探、可被爆破。请保持 `host: 127.0.0.1`，用 SSH 隧道访问；确有远程需求时至少套一层带 HTTPS 的反向代理 + 强密码。

**权限不足（Permission denied）怎么办？**
面板进程需要对 `Master/`、`Caves/` 目录（不只是文件，原子写入需要在目录里创建临时文件）和 `backup.directory` 有写权限。最简单的方案是用运行 DST 的同一个用户（通常是 `steam`）运行面板；或者
`sudo chown -R steam:steam /home/steam/.klei/DoNotStarveTogether`。

**提示 “no Lua interpreter found”？**
`sudo apt install lua5.4`，或在 `config.yaml` 的 `lua.command` 里写解释器的完整路径。

**文本输入框里的值是什么类型？**
无预设选项的配置项渲染为文本框，保存时按以下规则转型：`true`/`false` → 布尔，`nil`/`null` → nil，纯数字 → 数字，其余 → 字符串；留空 → 使用 modinfo 默认值；想强制存字符串可以加英文双引号（如 `"123"` 会存成字符串 `123`）。

**modoverrides.lua 里有 modinfo 没有的选项会怎样？**
它们在配置面板下方以只读方式列出，保存时原样保留，不会丢失。

**添加 mod 后为什么显示 “not downloaded yet”？**
Web 面板本身不下载 mod——它只把 id 写进 `dedicated_server_mods_setup.lua`，由 DST 专用服务器在下次启动时从 Steam Workshop 下载。重启服务器后刷新页面，mod 就会带着名称和配置项出现在 Downloaded 列表里。

**某个 shard 的 modoverrides.lua 被改坏了怎么办？**
面板会拒绝保存该 shard（防止覆盖），页面顶部显示解析错误。到 Backups 页面
恢复一个备份即可。

## 后续可以增强的方向

- 从 Steam Workshop API 拉取 mod 标题/图标/更新时间，检测 mod 更新
  （添加 mod 后下载完成前就能显示名称）
- 编辑 `cluster.ini` / `server.ini`、查看服务器日志
- 一键“从 Master 复制配置到 Caves”
- WebSocket 实时显示重启命令输出
- 多集群（多 cluster_path）支持
