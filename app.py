from flask import Flask, render_template, request, jsonify
import json
import os

BASE_DIR     = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "application", "templates")
STATIC_DIR   = os.path.join(BASE_DIR, "application", "static")
DATA_FILE    = os.path.join(BASE_DIR, "application", "data", "team1_section4.json")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

if os.environ.get("EXPLAIN_TEMPLATES") == "1":
    app.config["EXPLAIN_TEMPLATE_LOADING"] = True
    # print the search path once so you can verify it points to application/templates
    print("JINJA SEARCH PATH:", app.jinja_loader.searchpath)

def load_team_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_member(slug: str):
    members = load_team_data()
    return next((m for m in members if m.get("slug") == slug), None)


# ---- routes ----
@app.route("/")
@app.route("/about")
def team_page():
    members = load_team_data()
    return render_template("team.html", members=members)


@app.route("/search")
def search():
    q = (request.args.get("q") or "").lower()
    members = load_team_data()
    results = [
        m for m in members
        if q in m.get("name", "").lower() or q in m.get("role", "").lower()
    ]
    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
