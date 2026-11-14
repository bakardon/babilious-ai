"""Start Babilious: python app.py  ->  http://127.0.0.1:5000"""
from babilious import config
from babilious.web import create_app

app = create_app()

if __name__ == "__main__":
    print(f"\n  Babilious is up at http://{config.HOST}:{config.PORT}  (Ctrl+C to stop)\n")
    # Werkzeug's threaded server streams replies token by token. It only listens
    # on localhost, which is all a personal assistant needs.
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
