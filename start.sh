pip install -r requirements.txt -q --break-system-packages
apt-get install -y qbittorrent-nox 2>/dev/null || true
python3 update.py && python3 -m bot
