from flask import Flask, jsonify, render_template, request, session
from babilious_engine import ask_babilious

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-in-production"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def get_history():
    return session.get("chat_history", [])


@app.get("/")
def home():
    return render_template("index.html", history=get_history())


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("message", "")).strip()

    if not question:
        return jsonify({"error": "Please enter a message."}), 400

    history = get_history()
    answer = ask_babilious(question)
    history.append({"question": question, "answer": answer})
    session["chat_history"] = history[-30:]

    return jsonify({"question": question, "answer": answer})


@app.post("/api/clear")
def clear_history():
    session.pop("chat_history", None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
