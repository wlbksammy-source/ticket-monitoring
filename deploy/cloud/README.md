# 云端无桌面运行

本目录是 fork 上的最小适配，许可证与仓库根目录的 Apache-2.0 `LICENSE` 相同。它不替换上游采集实现，也不打开内置定时调度。

父级已有的每日两次 routine 负责何时执行 `run-once.sh`。这里不添加 cron、systemd 或第二条日程。

## 已有行为（本次核对过的源码）

- `schedule` 里没有开关时，`main.py` 会调用 `run_scheduler`，进程一直停在那里。`interval_minutes: 10080` 只把下一轮推迟约 7 天。
- 文档中的 `--once` 原先只是不启动网页，然后仍然进入调度器。现在会在一轮结束后退出；采集未打开或登录标记不足时，在下载浏览器之前就退出。
- 携程、同程、途牛、去哪儿网页的请求参数写死为 1 成人、0 儿童。飞猪列表 URL 没有乘客参数。去哪儿网页渠道在代码里标记为停服。
- `direct_only: true` 只把中转心理价写成 0，不改抓取 URL。同程 URL 仍是 `direct=0`。携程国内列表仍带 `cabin=y_s_c_f`，不是只查经济舱。
- 告警代码里的 `FLIGGY_TAX_PAD` 为 0，不再加 100。这不表示列表价已经是指定乘客的含税全家价。

因此结果文件里 `family_total_cny` 固定为 `null`，`family_quote_ok` 固定为 `false`。有列表价时角色是 `adult_reference`（成人参考，总价未核验）。程序不会把成人价乘以人数，也不会把儿童单价加回去冒充全家价。

## 命令

在仓库根目录执行：

```bash
bash deploy/cloud/install.sh          # 只装 PyYAML 与虚拟环境，不常驻
bash deploy/cloud/health.sh           # 写 data/cloud-health.json
bash deploy/cloud/start.sh            # 调度未关闭时拒绝；关闭时写 idle 文件后退出
bash deploy/cloud/run-once.sh         # 单次。默认配置不会访问机票网站
bash deploy/cloud/stop.sh             # 没有 pid 文件时什么都不杀
```

`install.sh --full` 才安装 `requirements.txt`。它仍然不下载浏览器，也不采集。

这些脚本会去掉 `TM_WEB_HOST`、`DISPLAY`、`XAUTHORITY`、`WAYLAND_DISPLAY`，避免沿用镜像里的 `0.0.0.0` 或当前会话的显示。它们不设置代理，不监听端口。

网页控制台默认关闭（`web.enabled: false`）。不要为了看页面把主机改成 `0.0.0.0`，也不要做隧道。

## 配置

`config.safe.example.yaml` 是虚构示例：上海虹桥到广州、2099-01-01、航线停用、渠道空、采集关、通知关、调度关。安装时只有在没有 `config.yaml` 的情况下才复制它，已有文件不会被覆盖。

真实航线和登录态只放在云机本地的 `config.yaml` 与 `user_data/`。这两个路径已被 `.gitignore` 排除（`config.yaml`、`user_data/`）。

要让单次命令真的去采集，需要同时满足：

1. `crawler.collect: true`
2. 至少一条 `enabled` 不是 false 的航线
3. `platforms` 里有浏览器渠道（不要把已停服的去哪儿网页或 Linux 上的 App 渠道当作可用源）
4. 对应 `user_data/<渠道>/session-state.json` 存在且不是空文件

第 4 步只检查文件在不在，**不打开文件**。所以标记存在也不等于登录仍有效。标记不存在时退出码是 3，状态 `login_required`，不会启动浏览器。

## 登录

不要把账号、密码、验证码或 cookie 交给程序或写进仓库。本适配不会调用 `--login`。

需要用户本人在有图形界面的桌面会话里操作。先在那个会话里确认 `DISPLAY`，再执行上游已有命令（会弹出浏览器，登录由用户自己完成）：

```bash
export DISPLAY=<桌面会话的显示号>
.venv/bin/python main.py --login ctrip -c config.yaml
```

`--login` 接受爬虫注册表里的名字。帮助文字写了 `ctrip`、`fliggy`、`tongcheng`；`tuniu` 同样在注册表里。飞猪 PC 与 H5 是两套 profile。`--login fliggy` 只打开飞猪爬虫自己的窗口，不一定写入 `user_data/fliggy_h5/`。单次采集目前只把 `user_data/<渠道>/session-state.json` 当作必要标记，H5 标记缺失会写进结果，但不会单凭这一点拒绝。H5 通路仍可能在采集时要求登录。

无显示、或用户不在场时，不要执行 `--login`。验证码、滑块和人脸由用户自己完成，程序不代过。

## 结果文件

| 文件 | 含义 |
| --- | --- |
| `data/cloud-health.json` | 调度、监听、通知、乘客口径。`exit 0` 只表示配置符合云端安全默认 |
| `data/cloud-idle.json` | `start.sh` 确认没有常驻进程 |
| `data/cloud-once.json` | 单次结果。没有列表价就不是查价成功 |
| `logs/cloud-ops.jsonl` | 上面几次写入的一行摘要，不含价格和凭据 |
| `logs/monitor.log` | 只有真正进入 `main.py` 时才会由上游日志器写入 |

`exit 0` 且 `status=collection_disabled` 表示按设计没有采集。`family_quote_ok` 仍是 false。

## 日志保留

`data/` 和 `logs/` 在安装目录里，不在本编码虚拟机上当作长期服务。父级把整份安装放在持久云机后，这两个目录才会留下来。
