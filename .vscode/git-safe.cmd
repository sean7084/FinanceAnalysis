@echo off
setlocal

set "REAL_GIT=%ProgramFiles%\Git\cmd\git.exe"
if not exist "%REAL_GIT%" set "REAL_GIT=%ProgramFiles%\Git\bin\git.exe"
if not exist "%REAL_GIT%" set "REAL_GIT=git"

if /I "%~1"=="pull" (
  for /f "delims=" %%i in ('"%REAL_GIT%" rev-parse --git-dir 2^>nul') do set "GIT_DIR=%%i"
  if defined GIT_DIR del /f /q "%GIT_DIR%\FETCH_HEAD" >nul 2>nul
)

"%REAL_GIT%" %*
exit /b %errorlevel%