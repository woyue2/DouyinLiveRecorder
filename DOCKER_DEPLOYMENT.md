# Docker 录制与百度云定时上传部署备忘录

> 状态：暂不启用，仅作为以后部署时的参考。
>
> 这份文档面向以后接手部署的人员或 Agent。正式启用前，应再次核对服务器系统、Docker 版本、Ubuntu 软件源中的 FFmpeg 版本以及项目当时的最新代码。

## 1. 推荐架构

推荐将录制和定时上传分开：

- Docker 容器负责运行录制程序、Python、Node.js 和 FFmpeg。
- 服务器宿主机负责运行 `crontab`。
- 宿主机上的 `upload_baidu.sh` 直接处理项目目录中的 `downloads`。
- 不要在录制主容器中同时启动 `cron`。

`docker-compose.yaml` 会将宿主机的 `./downloads` 挂载为容器内的 `/app/downloads`，因此容器录制出来的文件可以直接被宿主机上传脚本访问。

这种方式有以下优点：

- 录制容器重建时不会丢失百度云 `bypy` 授权信息。
- 上传任务失败不会导致录制主进程退出。
- 定时任务可以直接通过宿主机的 `crontab` 查看和维护。
- Docker 镜像中不需要再运行第二个长期驻留的 `cron` 进程。

## 2. 预期服务器目录

本文假设项目部署在：

```text
/home/ubuntu/DouyinLiveRecorder-main
```

主要目录如下：

```text
/home/ubuntu/DouyinLiveRecorder-main/
├── config/
├── logs/
├── backup_config/
├── downloads/
├── Dockerfile
├── docker-compose.yaml
├── requirements.txt
├── upload_baidu.sh
└── upload.log
```

如果实际部署路径不同，必须同步修改：

- `upload_baidu.sh` 中的 `DOWNLOAD_DIR`
- `upload_baidu.sh` 中的 `LOG_FILE`
- `crontab` 中的脚本路径和日志路径

## 3. 建议使用的 Dockerfile

项目曾验证 Ubuntu 22.04 软件源提供的 FFmpeg 4.4.2 可以正常工作，并记录了 FFmpeg 6.1 static 不适用的情况。因此建议使用 Ubuntu 22.04 作为基础镜像，并在构建时检查 FFmpeg 版本和 `libmp3lame` 编码器。

正式启用时，可以将仓库中的 `Dockerfile` 调整为：

```dockerfile
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        python3 \
        python3-pip \
        ffmpeg \
        tzdata && \
    ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata && \
    ffmpeg -version | head -n 1 | grep -q "ffmpeg version 4.4.2" && \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "libmp3lame" && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

CMD ["python3", "main.py"]
```

注意事项：

- Ubuntu 22.04 默认提供 Python 3.10，满足当前项目 `Python >= 3.10` 的要求。
- `ubuntu:22.04` 和 APT 软件源仍可能随时间更新。构建中的版本检查可以在环境不再符合预期时直接让构建失败，避免静默使用未经验证的 FFmpeg。
- 如果未来项目明确要求 Python 3.11 或更高版本，应重新评估基础镜像，而不是删除版本检查后直接构建。
- 如果 Ubuntu 22.04 软件源不再返回 FFmpeg 4.4.2，应先测试新版本，不要直接绕过检查。

## 4. 建议使用的 docker-compose.yaml

如果需要运行当前仓库中的代码，必须使用本地构建，不能继续只引用远端的 `ihmily/douyin-live-recorder:latest`。

建议配置：

```yaml
services:
  app:
    build:
      context: .
    image: douyin-live-recorder:fix-mp3
    environment:
      TERM: xterm-256color
      TZ: Asia/Shanghai
    tty: true
    stdin_open: true
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - ./backup_config:/app/backup_config
      - ./downloads:/app/downloads
    restart: unless-stopped
```

不要同时保留下面这种只拉取远端镜像、没有 `build` 的配置：

```yaml
image: ihmily/douyin-live-recorder:latest
# build: .
```

否则启动的可能是远端旧版本，不包含本地 MP3 修复。

## 5. 构建和启动

进入项目目录：

```bash
cd /home/ubuntu/DouyinLiveRecorder-main
```

首次构建或基础环境发生变化时：

```bash
docker compose build --no-cache
docker compose up -d
```

普通代码更新后可以执行：

```bash
docker compose up -d --build
```

查看运行状态：

```bash
docker compose ps
docker compose logs -f app
```

停止服务：

```bash
docker compose down
```

## 6. 构建后的 FFmpeg 检查

启动后必须确认 FFmpeg 版本：

```bash
docker compose exec app ffmpeg -version
```

确认 MP3 编码器存在：

```bash
docker compose exec app \
  ffmpeg -hide_banner -encoders 2>/dev/null | grep libmp3lame
```

预期结果：

- FFmpeg 第一行显示 `4.4.2`。
- 编码器列表中包含 `libmp3lame`。

还应进行一次短时间的实际录制测试，并确认：

- `downloads` 中最终生成 `.mp3` 或其他所选格式。
- 录制过程中使用的 `.part` 文件在成功结束后被正确发布。
- FFmpeg 日志中没有编码器、分段格式或输出路径错误。
- 宿主机和容器内看到的是同一份 `downloads` 内容。

## 7. 在宿主机安装百度云上传依赖

上传任务建议直接运行在宿主机。

安装 `bypy`：

```bash
python3 -m pip install --user bypy
```

安装脚本所需系统工具：

```bash
sudo apt update
sudo apt install -y lsof util-linux
```

其中：

- `lsof` 用于避免移动仍被录制进程占用的文件。
- `flock` 由 `util-linux` 提供，用于避免两个上传任务重叠运行。

如果 `python3 -m bypy` 无法找到用户级安装的包，应检查运行 `crontab` 的用户与执行安装的用户是否一致。

## 8. 百度云授权

必须使用将来实际运行 `crontab` 的同一个 Linux 用户完成授权。例如计划使用 `ubuntu` 用户运行定时任务，就不要使用 `root` 完成授权。

执行：

```bash
python3 -m bypy info
```

根据终端提示完成百度云授权。

授权完成后再次执行：

```bash
python3 -m bypy info
```

确认能够正常读取百度云信息。

`bypy` 的授权文件通常保存在执行用户的主目录中。不要在没有备份或重新授权准备的情况下删除该用户的 `bypy` 配置。

## 9. 配置上传脚本

确认 `upload_baidu.sh` 中的路径与服务器实际路径一致：

```bash
MY_EMAIL="你的邮箱@qq.com"
DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOG_FILE="/home/ubuntu/DouyinLiveRecorder-main/upload.log"
LOCK_FILE="/tmp/douyin_live_recorder_upload.lock"
```

赋予执行权限：

```bash
chmod +x /home/ubuntu/DouyinLiveRecorder-main/upload_baidu.sh
```

正式加入定时任务前，先手动测试：

```bash
/bin/bash /home/ubuntu/DouyinLiveRecorder-main/upload_baidu.sh
```

上传脚本当前的关键行为：

- 只扫描正式发布的录制格式。
- 不匹配仍在写入的 `*.part` 文件。
- 使用 `lsof` 跳过仍被进程占用的文件。
- 将待上传文件移动到 `downloads/converted`。
- 成功同步到百度云后删除本轮的 `converted`。
- 上传失败时保留文件，等待下一轮重试。
- 使用 `flock` 防止任务重叠。

## 10. 配置宿主机 crontab

使用运行上传任务的普通用户执行：

```bash
crontab -e
```

每五分钟执行一次：

```cron
*/5 * * * * /bin/bash /home/ubuntu/DouyinLiveRecorder-main/upload_baidu.sh >> /home/ubuntu/DouyinLiveRecorder-main/upload.log 2>&1
```

查看当前定时任务：

```bash
crontab -l
```

观察上传日志：

```bash
tail -f /home/ubuntu/DouyinLiveRecorder-main/upload.log
```

如果定时任务无法运行，依次检查：

1. `crontab` 所属用户是否正确。
2. 该用户是否已经完成 `bypy` 授权。
3. `python3 -m bypy info` 是否可以在非交互环境正常执行。
4. 项目路径和日志路径是否正确。
5. `upload_baidu.sh` 是否有执行权限。
6. `lsof`、`flock` 和 `python3` 是否可用。
7. 磁盘空间和目录权限是否正常。

## 11. 更新与重建流程

以后更新项目代码时：

```bash
cd /home/ubuntu/DouyinLiveRecorder-main
git pull
docker compose build --no-cache
docker compose up -d
```

更新前应备份：

- `config/`
- `backup_config/`
- 尚未上传的 `downloads/`
- 宿主机用户的 `bypy` 授权配置
- 实际使用的 `docker-compose.yaml`

容器重建不应删除宿主机挂载的 `downloads`、`config`、`logs` 和 `backup_config`。不要使用未确认目标范围的递归删除命令清理这些目录。

## 12. 以后接手的 Agent 检查清单

正式启用前，后续 Agent 应完成以下检查：

- [ ] 查看仓库最新提交，确认 Dockerfile、依赖和启动入口是否发生变化。
- [ ] 查看 `pyproject.toml` 的最低 Python 版本要求。
- [ ] 查看 MP3 录制命令是否仍使用 `libmp3lame`。
- [ ] 查看项目是否仍要求或使用 Node.js。
- [ ] 确认 Ubuntu 22.04 构建得到的 FFmpeg 是预期版本。
- [ ] 构建镜像并运行项目测试。
- [ ] 进行一次真实的短时 MP3 录制测试。
- [ ] 检查 `.part` 文件发布和分段录制行为。
- [ ] 确认 Compose 使用本地构建镜像，而不是远端旧镜像。
- [ ] 确认 `downloads` 使用宿主机目录挂载。
- [ ] 确认上传脚本中的绝对路径与服务器一致。
- [ ] 使用运行 `crontab` 的同一用户完成 `bypy` 授权。
- [ ] 手动运行一次上传脚本后再启用定时任务。
- [ ] 确认上传失败时文件仍保留在 `downloads/converted`。
- [ ] 确认录制任务和上传任务不会同时操作未完成的 `.part` 文件。

## 13. 最终推荐

当前推荐部署组合为：

```text
Docker 容器：
  Ubuntu 22.04
  Python 3.10
  FFmpeg 4.4.2
  libmp3lame
  Node.js 20
  DouyinLiveRecorder

服务器宿主机：
  crontab
  bypy
  lsof
  flock
  upload_baidu.sh
```

除非以后确实需要完全容器化上传任务，否则不要在录制主容器中加入 `cron`。如果以后需要容器化上传，应创建独立的 `uploader` 服务，并继续由宿主机定时调度，避免将录制进程、定时器和上传进程全部放入同一个容器。
