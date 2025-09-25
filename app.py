from flask import Flask, render_template, request, jsonify, abort
import json
import os
import logging
from functools import lru_cache

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "application", "templates")
STATIC_DIR = os.path.join(BASE_DIR, "application", "static")
DATA_FILE = os.path.join(BASE_DIR, "application",
                         "data", "team1_section4.json")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.logger.setLevel(logging.INFO)
log = app.logger

@lru_cache(maxsize=1)
def _load_cached(mtime):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_team_data():
    try:
        mtime = int(os.path.getmtime(DATA_FILE))
        return _load_cached(mtime) 
    except (OSError, json.JSONDecodeError):
        abort(500)


def find_member(slug: str):
    members = load_team_data()
    return next((m for m in members if m.get("slug") == slug), None)

@app.route("/")  
def home_page():  
    return render_template("home.html")

@app.route("/about") 
def team_page():
    members = load_team_data()
    return render_template("team.html", members=members)

@app.route("/search")  
def search_page():  
    return render_template("search.html")

@app.get("/api/search") 
def api_search():  
    q = (request.args.get("q") or "").strip()
    if not q or len(q) > 200:
        return jsonify([])
    q = q.casefold()
    log.info("Search query: %s", q)
    members = load_team_data()
    results = [
        m for m in members
        if q in f"{m.get('name','')} {m.get('role','')}".casefold()
    ]
    return jsonify(results)

@app.route("/about/<slug>")
def member_page(slug: str):
    person = find_member(slug)
    if not person:
        log.warning("Member not found: %s", slug)
        abort(404)

    person.setdefault("subtitle", "")
    person.setdefault("bio", "")
    person.setdefault("skills", [])
    person.setdefault("contributions", [])
    person.setdefault("github", "")
    person.setdefault("linkedin", "")
    person.setdefault("email", "")

    return render_template("member.html", person=person)

# Placeholder routes for login, signup, and becoming a tutor
@app.route("/login")  
def login():  
    return render_template("login.html")  

@app.route("/signup")  
def signup():  
    return render_template("signup.html")  

@app.route("/tutors/new")  
def become_tutor():  
    return render_template("become_tutor.html")

@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(_e):
    return render_template("500.html"), 500

if __name__ == "__main__":
    app.run(debug=True, port=5001)
