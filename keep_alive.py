# keep_alive.py
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.get("/")
def home():
    return "OK", 200

def _run():
    # Replit expects something listening on 0.0.0.0
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    Thread(target=_run, daemon=True).start()
