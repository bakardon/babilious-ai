from flask import Flask, render_template, request, session

from babilious_engine import ask_babilious

app = Flask(__name__)
app.secret_key = "babilious_secret"

@app.route("/", methods=["GET", "POST"])
def home():

    if "chat_history" not in session:
        session["chat_history"] = []

    if request.method == "POST":

        question = request.form.get("question")

        if question:

            answer = ask_babilious(question)

            history = session["chat_history"]

            history.append({
                "question": question,
                "answer": answer
            })

            session["chat_history"] = history

    return render_template(
        "index.html",
        history=session["chat_history"]
    )


if __name__ == "__main__":
    app.run(debug=True)