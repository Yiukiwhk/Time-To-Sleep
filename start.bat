@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] 检查依赖...
"%PY%" -c "import fastapi, uvicorn, sqlalchemy, pydantic, bcrypt" 2>nul
if errorlevel 1 (
    echo 依赖缺失，正在安装 ^(requirements.txt^)...
    "%PY%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo 安装失败：请手动执行  "%PY%" -m pip install -r requirements.txt
        echo 也可使用离线安装器： "%PY%" scripts\bootstrap_deps.py
        pause
        exit /b 1
    )
)

echo [2/2] 启动服务...
"%PY%" "%~dp0run.py" --host 0.0.0.0 --port 8000 %*
endlocal
pause
