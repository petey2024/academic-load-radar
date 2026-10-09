# 公网部署与真实网址

## 当前临时演示地址

已验证地址：<https://3b162423103984.lhr.life>

验证时间：2026-10-09（中国标准时间）。`/api/health` 返回 `{"status":"ok","service":"academic-load-radar"}`。该地址是 localhost.run 临时隧道，电脑上的本地服务或隧道进程停止后地址会失效，不适合作为长期发布地址。

项目可以部署到支持 Python Web 服务的托管平台。仓库根目录的 `render.yaml` 是 Render Blueprint 配置，包含以下部署约束：

- 启动命令为 `python -m app.main`；
- 服务绑定 `0.0.0.0`，读取平台提供的 `PORT`；
- SQLite 数据目录为 `/var/data`；
- 使用持久化磁盘保存任务，避免普通重启后数据丢失。

## 使用 Render 部署

1. 将本仓库推送到 GitHub。
2. 在 Render 控制台选择 **New > Blueprint**，连接该 GitHub 仓库。
3. Render 读取 `render.yaml` 后创建 Web Service 和持久化磁盘。
4. 等待部署完成，在服务页面复制 `https://<service-name>.onrender.com`。
5. 用浏览器打开该地址，并访问 `/api/health`；返回 `{"status":"ok"}` 才算部署成功。
6. 将这个实际地址填入 `README.md`、`docs/wiki/02-原型设计.md` 和需求分析报告的发布记录。

## 提交材料中的地址证据

提交时需要保留以下证据：

- GitHub 仓库地址；
- 公网应用地址；
- 公网地址的 `/api/health` 响应截图或终端记录；
- 首页截图和至少一条新增任务、风险计算、完成任务流程截图；
- 部署日期、服务平台和持久化目录说明。

仓库只能记录已经验证过的地址，不能提前填写猜测的域名。Render 服务名或地址变更后，应同步更新所有课程材料。

## 本地运行

本地默认仍绑定 `127.0.0.1:8000`。模拟托管平台的启动方式：

```powershell
$env:HOST = "0.0.0.0"
$env:PORT = "8000"
$env:ACADEMIC_LOAD_RADAR_DATA = "$PWD\.local-data"
python -m app.main
```
