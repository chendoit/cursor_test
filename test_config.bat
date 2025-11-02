@echo off
chcp 65001 >nul
echo ============================================
echo 配置测试脚本
echo ============================================
echo.
echo 此脚本将测试你的 .env 配置是否正确
echo.

call venv\Scripts\activate.bat
python test_config.py

echo.
pause

