python3 update.py
pip install -r requirements.txt --break-system-packages
apt-get install -y qbittorrent-nox 2>/dev/null || true
python3 -m bot
