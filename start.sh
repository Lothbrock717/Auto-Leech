apt-get install -y qbittorrent-nox 2>/dev/null || true
pip install -r requirements.txt -q 2>/dev/null || true
python3 update.py && python3 -m bot
