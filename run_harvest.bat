@echo off
rem 计划任务入口：每天定时跑一次 YouTube 收割器
rem 手动测试：直接双击，或 schtasks /Run /TN "FakeNews-YT-Harvest"
set PYTHONIOENCODING=utf-8
rem googleapis 直连不通，API 调用走本地代理（已设则不覆盖）
if not defined HTTPS_PROXY set HTTPS_PROXY=http://127.0.0.1:7897
cd /d %~dp0
fake_news_env\Scripts\python.exe prototype\harvest_daily.py >> logs\harvest_task.log 2>&1
