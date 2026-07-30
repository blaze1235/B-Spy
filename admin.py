import hmac

from flask import Flask, Response, redirect, render_template, request

import config
import db

app = Flask(__name__)

PLAN_OPTIONS = ["free", "pro", "unlimited"]


def _check_auth(username, password):
    return hmac.compare_digest(username or "", config.ADMIN_USER) and hmac.compare_digest(
        password or "", config.ADMIN_PASSWORD
    )


@app.before_request
def require_login():
    auth = request.authorization
    if not auth or not _check_auth(auth.username, auth.password):
        return Response(
            "Login required", 401, {"WWW-Authenticate": 'Basic realm="B-Spy Admin"'}
        )


@app.route("/")
def index():
    return redirect("/admin")


@app.route("/admin")
def admin_dashboard():
    users = db.list_connections_with_stats()
    totals = db.get_totals()
    return render_template("admin.html", users=users, totals=totals, plan_options=PLAN_OPTIONS)


@app.route("/admin/plan/<connection_id>", methods=["POST"])
def update_plan(connection_id):
    plan = request.form.get("plan", "free")
    if plan not in PLAN_OPTIONS:
        plan = "free"
    db.set_plan(connection_id, plan)
    return redirect("/admin")


def run():
    app.run(host="0.0.0.0", port=config.PORT, threaded=True)
