from flask import Flask, render_template, request, jsonify, abort
import json
import os

BASE_DIR     = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "application", "templates")
STATIC_DIR   = os.path.join(BASE_DIR, "application", "static")
DATA_FILE    = os.path.join(BASE_DIR, "application", "data", "team1_section4.json")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

def load_team_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        abort(500)

def find_member(slug: str):
    members = load_team_data()
    return next((m for m in members if m.get("slug") == slug), None)

@app.route("/")
@app.route("/about")
def team_page():
    members = load_team_data()
    return render_template("team.html", members=members)

@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])
    members = load_team_data()
    results = [
        m for m in members
        if q in m.get("name", "").lower() or q in m.get("role", "").lower()
    ]
    return jsonify(results)

@app.route("/about/<slug>")
def member_page(slug: str):
    person = find_member(slug)
    if not person:
        abort(404)

    person.setdefault("subtitle", "")
    person.setdefault("bio", "")
    person.setdefault("skills", [])
    person.setdefault("contributions", [])
    person.setdefault("github", "")
    person.setdefault("linkedin", "")
    person.setdefault("email", "")

    return render_template("member.html", person=person)

if __name__ == "__main__":
    app.run(debug=True, port=5001)
