from flask import Flask, render_template, request, session
from babilious_engine import ask_babilious

app = Flask(__name__)
app.secret_key = "babilious-local-dev-key"


@app.route("/", methods=["GET", "POST"])
def home():
    history = session.get("chat_history", [])

    if request.method == "POST":
        question = request.form.get("question", "").strip()

        if question:
            answer = ask_babilious(question)
            history.append({"question": question, "answer": answer})
            session["chat_history"] = history[-20:]

    return render_template("index.html", history=session.get("chat_history", []))


@app.post("/clear")
def clear_history():
    session.pop("chat_history", None)
    return ("", 204)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
