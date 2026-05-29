# ============================================
# LOL 每日资讯 - 定时任务配置说明
# ============================================

## macOS (launchd) - 推荐

# 1. 编辑 plist 文件，替换 YOUR_API_KEY_HERE 为真实 API Key
# 2. 复制到 LaunchAgents 目录:
cp com.lol.dailynews.plist ~/Library/LaunchAgents/

# 3. 加载定时任务:
launchctl load ~/Library/LaunchAgents/com.lol.dailynews.plist

# 4. 验证是否加载成功:
launchctl list | grep com.lol.dailynews

# 5. 手动触发一次测试:
launchctl start com.lol.dailynews

# 6. 如需卸载:
launchctl unload ~/Library/LaunchAgents/com.lol.dailynews.plist

# 修改执行时间: 编辑 plist 中的 Hour/Minute 即可


## Linux (crontab)

# 编辑 crontab:
crontab -e

# 添加以下行（每天 9:30 执行）:
# 30 9 * * * cd /path/to/lol-news-auto && /usr/bin/python3 main.py >> cron.log 2>&1


## 依赖安装

pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入 ANTHROPIC_API_KEY
