@echo off
setlocal

cd /d "%~dp0"

set "CONDA_BAT="

if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
if exist "C:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\miniconda3\condabin\conda.bat"
if exist "C:\ProgramData\anaconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\anaconda3\condabin\conda.bat"

if defined CONDA_BAT (
    call "%CONDA_BAT%" activate base
) else (
    call conda activate base
)

if errorlevel 1 (
    echo Failed to activate the base conda environment.
    echo Make sure Anaconda or Miniconda is installed and conda is available from Command Prompt.
    pause
    exit /b 1
)

python run_pattern_builder_gui.py
if errorlevel 1 (
    echo.
    echo Pattern Builder GUI exited with an error.
    pause
    exit /b 1
)

endlocal
