# 验收记录

记录时间：2026-11-17（本编码虚拟机的系统时间）。这里不是持久云机，下面的命令退出不等于已经部署，也没有查到任何机票价格。

环境：Linux x86_64，Python 3.12.3。上游版本字符串仍是 `1.7.6`。测试用的是 `deploy/cloud/config.safe.example.yaml` 里的虚构航线（SHA→CAN，2099-01-01），不是真实订单。

## 自动化测试

在安装出的 `.venv` 里执行：

```text
.venv/bin/python -m pytest tests/test_cloud_adapt.py -q --tb=line
.................                                                        [100%]
17 passed in 0.40s
```

这 17 项覆盖：`interval_minutes: 10080` 仍算调度开着；`schedule.enabled: false` 才关掉；安全模板没有外发、不监听、全家价为 null；拒绝把成人价乘人数或保留调用方塞入的总价；会话文件里的测试字符串不会出现在结果里；登录标记缺失时不调用上游采集。

没有跑仓库里原有的整套测试。那些用例还依赖 OpenCV、模拟器等，本轮没有安装。

`import main` 在装了 PyYAML、httpx、playwright、APScheduler、Pillow 之后成功，版本打印为 `1.7.6`。没有执行 `playwright install`，没有下载浏览器内核。

## 脚本实跑

`bash deploy/cloud/install.sh` 创建 `.venv`、安装 PyYAML，并因为当时没有 `config.yaml` 而写入了虚构模板。随后：

| 命令 | 退出码 | 原文要点 |
| --- | --- | --- |
| `bash deploy/cloud/health.sh` | 0 | `status=safe exit=0 family_quote_ok=false schedule_enabled=false` |
| `bash deploy/cloud/start.sh` | 0 | `未启动常驻进程。内置调度保持关闭。` |
| `bash deploy/cloud/run-once.sh` | 0 | `status=collection_disabled`，`quotes=0`，`未访问任何机票网站` |
| `bash deploy/cloud/stop.sh` | 0 | `没有由 deploy/cloud 启动的常驻进程` |

`data/cloud-health.json` 中：`version` 为 `1.7.6`，`schedule_enabled` 为 false，`web_enabled` 为 false，`bind_host` 为 `127.0.0.1`，`family_total_status` 为 `unverified`，`passengers_applied_to_fetch` 为 false，`crawl_contract_matches` 为 true，`problems` 为空。`real_quote_completed` 为 false。

另外用临时配置把采集打开、航线打开、且不放会话标记，执行 `python main.py --once`。进程在进入浏览器安装之前退出，码为 3，状态 `login_required`，`browser_launched` 为 false。输出写明没有读取 cookie，也没有自动登录。

`python main.py -c config.yaml`（安全模板）直接退出，码为 0，日志是「内置调度已禁用，进程退出」。结束后没有 `main.py` 或 `TicketMonitor` 进程，也没有 8765 监听。

## 没有完成的事

- 没有真实报价，也没有核验 2 成人 + 10 岁儿童的人民币含税全家价。源码请求仍是 1 成人、0 儿童。
- 没有登录任何账号，没有读取 `user_data` 里的会话内容。
- 没有把服务留在本虚拟机上。父级需要在持久云机上解包或拉本分支后再执行安装脚本。
