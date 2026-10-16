# 特定生产环境：YouTube 住宅代理出口

这个方案让 StashSeek Chat 的 YouTube 元数据、字幕和 ASR 音频请求，经服务器本机的
tinyproxy 转发到付费住宅代理。和[家庭网络出口](youtube-home-egress.md)相比，它不依赖
Mac 开机或终端常开，可以长期运行。

应用侧契约不变：Worker 仍然只看到无凭据的 `YOUTUBE_PROXY_URL=http://127.0.0.1:18080`。
代理商的账号密码只写在 tinyproxy 配置里，通过 systemd `LoadCredential` 交给一个临时
系统用户，不进入 `notebook-agent.env`、数据库、日志或命令行。数据库、Redis、MinIO、
Embedding、邮件、Web、MCP 和 Caddy 都不走这个代理。

tinyproxy 只放行到 `youtube.com` / `googlevideo.com` 的 HTTPS CONNECT 443，其余目的地
一律 403，所以服务器上其他进程即使连到这个端口，也花不到代理流量。TLS 是端到端的，
代理商只能看到目的主机名和字节数。

## 与家庭出口互斥

两种出口都占用服务器的 `127.0.0.1:18080`，Worker 配置完全相同，同一时间只能运行一个。
`scripts/youtube-home-egress` 会在启动前检查服务器端口，端口被占用时直接退出，不会
误开第二条隧道。切换方法见下文“切换到家庭出口”。

## 购买与准备凭据

当前使用 Evomi 的 Core Residential。

1. 购买**按量付费**（pay as you go）的 Core Residential，流量不过期。不要开
   Core Membership 月付套餐（$49.99/月含 100GB，远超我们的用量）。Core Membership
   的免费试用只有 1 天，到期会自动转成月付，试用期内要在 Subscription 页点
   **Stop auto-renewal**。
2. 在 Core Residential 的 **Proxy Generator** 里按下表设置，生成一条代理串：

   | 选项 | 设置 |
   | --- | --- |
   | Protocol | HTTP |
   | Country | United States of America（City/Region 保持 Random） |
   | Type | Sticky Session → Normal Session |
   | Amount / Lifetime | 1 / 30 |
   | Expert Settings | 全部 Disabled（每项都会把流量费乘 1.4–6 倍） |

   - 必须用 sticky：YouTube 返回的字幕和音频地址会绑定请求时的 IP，而元数据和字幕在
     两个子进程里分别请求，每个连接都换 IP 的轮换模式会让后一步失败。
   - 不选 Hard Session：它会把同一个 IP 用尽可能久，更容易被 YouTube 标记。
   - 选美国：避免随机落到欧盟触发 cookie 同意页，也避开个别视频的地区限制。
3. 生成的串形如
   `http://core-residential.evomi.com:1000:<用户名>:<密码>_country-US_session-<随机串>`。
   国家和 sticky 参数拼在密码后面，Lifetime 为默认的 30 时不出现在串里。
4. 检查凭据格式：tinyproxy 要求**用户名不含 `:`，密码不含 `@` 和空白**。不符合时先在
   Evomi 后台 Reset Proxy Key。
5. 在 Evomi 后台关注余额，必要时设置提醒。

## 一次性安装

以下命令在包含本手册的 release 已部署之后执行，工作目录为
`/opt/notebook-agent/current`。

1. 先屏蔽发行版自带的 `tinyproxy.service`，再安装 tinyproxy。这样软件包装好后不会
   自动在 8888 端口起一个默认实例：

   ```bash
   sudo systemctl mask tinyproxy.service
   sudo apt update
   sudo apt install tinyproxy
   systemctl is-enabled tinyproxy.service
   tinyproxy -v
   ```

   `is-enabled` 应输出 `masked`；单元还不存在时，`mask` 会提示
   `Unit tinyproxy.service does not exist, proceeding anyway`，这是正常的。版本需为
   1.11.0 或更高（生产环境 Ubuntu 26.04 为 1.11.3）。保持 `apt upgrade` 获取安全补丁；
   本服务只监听回环地址，暴露面仅限本机。

2. 安装配置和过滤规则（目录和文件都只允许 root 读取）：

   ```bash
   cd /opt/notebook-agent/current
   sudo install -d -o root -g root -m 0700 /etc/notebook-agent/youtube-egress
   sudo install -o root -g root -m 0600 deploy/youtube-egress/tinyproxy.filter \
     /etc/notebook-agent/youtube-egress/tinyproxy.filter
   sudo install -o root -g root -m 0600 deploy/youtube-egress/tinyproxy.conf.example \
     /etc/notebook-agent/youtube-egress/tinyproxy.conf
   ```

3. 用 `sudoedit` 只修改最后一行 `Upstream`，把生成器的串改写成 tinyproxy 格式：

   ```bash
   sudoedit /etc/notebook-agent/youtube-egress/tinyproxy.conf
   ```

   ```text
   Upstream http <用户名>:<密码>_country-US_session-<随机串>@core-residential.evomi.com:1000
   ```

   密码那一段连同 `_country-US_session-...` 整段照抄。不要把凭据写在命令行、shell
   历史、`notebook-agent.env` 或任何提交里。

4. 放行 AppArmor。Ubuntu 的 apparmor 软件包为 `/usr/bin/tinyproxy` 带了一份 profile，
   只允许读取 `/etc/tinyproxy/tinyproxy.conf`；而 unit 通过 `LoadCredential` 把配置放在
   `/run/credentials/...` 下，不放行时服务会报 `Could not open config file` 并退出。
   该 profile 末尾会 include `local/tinyproxy`，在这里只追加两条只读规则：

   ```bash
   test -f /etc/apparmor.d/tinyproxy && echo "profile present"
   test ! -e /etc/apparmor.d/local/tinyproxy || cat /etc/apparmor.d/local/tinyproxy
   sudo install -o root -g root -m 0644 deploy/youtube-egress/apparmor-local-tinyproxy \
     /etc/apparmor.d/local/tinyproxy
   sudo apparmor_parser -r /etc/apparmor.d/tinyproxy
   ```

   如果第一条没有输出 `profile present`，说明系统没有这份 profile，跳过本步。如果第二条
   打印出了已有内容，说明有人自定义过这个文件：把仓库里的两行追加进去，不要覆盖。

5. 安装、校验并启动 unit：

   ```bash
   sudo install -m 0644 deploy/systemd/notebook-agent-youtube-egress.service \
     /etc/systemd/system/
   sudo systemd-analyze verify /etc/systemd/system/notebook-agent-youtube-egress.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now notebook-agent-youtube-egress.service
   sudo systemctl is-active notebook-agent-youtube-egress.service
   ss -ltn '( sport = :18080 )'
   ```

   `ss` 的本地地址必须是 `127.0.0.1:18080`，不能是 `0.0.0.0`、`[::]` 或公网地址。

## 激活前的 Canary

先确认过滤生效，再确认 YouTube 可达：

```bash
curl -sS -o /dev/null -x http://127.0.0.1:18080 https://example.com
curl -sS -o /dev/null -w '%{http_code}\n' -x http://127.0.0.1:18080 https://www.youtube.com/
```

第一条必须失败，提示 `CONNECT tunnel failed, response 403`；第二条应返回 `200`。

再用 Worker 同款 yt-dlp 参数，拿一个**有字幕的公开视频**确认元数据和字幕列表：

```bash
sudo -u notebook-agent env \
  HTTPS_PROXY=http://127.0.0.1:18080 HTTP_PROXY=http://127.0.0.1:18080 \
  NO_PROXY=127.0.0.1,localhost,::1 \
  /opt/notebook-agent/current/.venv/bin/python -m yt_dlp --ignore-config \
  --extractor-args youtube:player_client=android_vr \
  --skip-download --list-subs 'https://www.youtube.com/watch?v=<公开视频ID>'
```

输出里应能看到 `Available subtitles` 或 `Available automatic captions` 列表。出现 `Sign in to confirm you're not a bot`、
429 或 403 时，先换一个 session 值（见“故障处理”）再试，不要直接激活。

## 生产 Worker 激活

用 `sudoedit` 在 `/etc/notebook-agent/notebook-agent.env` 中确认存在（家庭出口用过的话
已经有了）：

```text
YOUTUBE_PROXY_URL=http://127.0.0.1:18080
```

重启使用 YouTube connector 的 Worker。ASR 下载音频也走同一个代理，已安装 ASR worker
时一并重启：

```bash
sudo systemctl restart notebook-agent-worker
sudo systemctl is-active notebook-agent-worker
if systemctl is-enabled --quiet notebook-agent-asr-worker 2>/dev/null; then
  sudo systemctl restart notebook-agent-asr-worker
  sudo systemctl is-active notebook-agent-asr-worker
fi
```

先提交一个公开视频，确认条目到达 `ready`、字幕非空，再到代理商后台确认产生了少量流量。
之后才恢复正常使用。重试之前失败的条目时一次只试一个，确认成功后再继续。

## 用量与费用

- 字幕：每个视频（元数据加字幕）约 0.5–2 MB。
- ASR 音频：优先选择 40–72 kbps 的音频流（YouTube 通常为格式 139，约 49 kbps），每音频小时约 22 MB。范围内没有可用流时，才回退到 `bestaudio`（约 128 kbps，每音频小时约 58 MB）。
- 粗略估算：每月 300 个有字幕视频加 20 小时无字幕视频，约 1.5 GB。

定期对照代理商后台的用量。用量明显偏高时，先查 tinyproxy 日志里是否有异常的连接量：

```bash
sudo journalctl -u notebook-agent-youtube-egress --since today
```

## 故障处理

| Worker 错误分类 | 常见原因 | 处理 |
| --- | --- | --- |
| `youtube_proxy_unavailable` | egress 服务没运行，或端口被家庭出口占用 | `systemctl status notebook-agent-youtube-egress`，查看 journal |
| `youtube_proxy_authentication_failed` | 上游返回 407：凭据错误、凭据含 `:`/`@`，或流量已用完 | 核对 `Upstream` 行和后台余额，改完 `systemctl restart` |
| `youtube_proxy_timeout` | 上游网关慢或不可达 | 稍后重试；持续出现就换 session 值或联系代理商 |
| `youtube_rate_limited` / `youtube_fetch_failed` | 当前 sticky 出口 IP 被 YouTube 标记 | 把 `Upstream` 里 `session-` 后面的值换成新的 6–10 位字母数字，重启 egress 服务 |

换 session 值或改凭据后只需重启 egress 服务，不必重启 Worker：

```bash
sudoedit /etc/notebook-agent/youtube-egress/tinyproxy.conf
sudo systemctl restart notebook-agent-youtube-egress
```

遇到 429、403、机器人挑战时不要批量重试，避免短时间内消耗大量流量。

egress 服务本身起不来、journal 里出现
`tinyproxy: Could not open config file "/run/credentials/..."` 时，多半是 AppArmor 拦截。
用下面的命令确认后，回到安装第 4 步补上 local override：

```bash
sudo journalctl -k --since "-10 min" --no-pager | grep 'apparmor="DENIED".*tinyproxy'
```

## 切换到家庭出口

```bash
sudo systemctl stop notebook-agent-youtube-egress
```

然后在 Mac 上按[家庭出口手册](youtube-home-egress.md)启动助手，`YOUTUBE_PROXY_URL`
不用改。切回住宅代理时，先在 Mac 上按 Ctrl-C 停掉助手，再
`sudo systemctl start notebook-agent-youtube-egress`。

## 停用与回滚

```bash
sudo systemctl disable --now notebook-agent-youtube-egress
sudoedit /etc/notebook-agent/notebook-agent.env
sudo systemctl restart notebook-agent-worker
```

在 `notebook-agent.env` 里删掉 `YOUTUBE_PROXY_URL`；已安装 ASR worker 时同样重启它。
不再使用时删除凭据目录，并在代理商后台重置密码：

```bash
sudo rm -r /etc/notebook-agent/youtube-egress
sudo rm /etc/systemd/system/notebook-agent-youtube-egress.service
sudo systemctl daemon-reload
sudo rm /etc/apparmor.d/local/tinyproxy
sudo apparmor_parser -r /etc/apparmor.d/tinyproxy
```

如果 `/etc/apparmor.d/local/tinyproxy` 里还有别人加的规则，只删掉本手册追加的两行，
不要删除整个文件。

回滚不修改数据库、对象存储、Redis、Caddy、Web/MCP 或其他服务。去掉代理后，生产 IP
仍会被 YouTube 拦截；回滚只恢复原路由，不保证字幕恢复。
