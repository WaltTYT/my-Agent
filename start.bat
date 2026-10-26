cd /d "%~dp0"
call "D:\study\miniconda\Scripts\activate.bat" my-agent
python -m agents.main %*
