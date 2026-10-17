"""Plotwise web app. Run:  python app.py   then open http://localhost:5050"""
import json
import os
import re
import sys

if os.name == "nt" and not sys.flags.utf8_mode:  # Windows: files hold µ, ψ, ² etc., so run in UTF-8 mode
    os.environ["PYTHONUTF8"] = "1"
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-X", "utf8"] + sys.argv))

import hmac

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from plotwise import llm
from plotwise.core import HOSTED, ROOT, SESSIONS, Session

app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("PLOTWISE_MAX_UPLOAD_MB", 60)) * 1024 * 1024
PASSWORD = os.environ.get("APP_PASSWORD", "")  # optional: keeps strangers off a hosted copy (and off its API key)


@app.before_request
def password_gate():
    if PASSWORD and request.path.startswith("/api/") and request.path != "/api/status":
        given = request.headers.get("X-Plotwise-Pass", "").encode()
        if not hmac.compare_digest(given, PASSWORD.encode()):
            return jsonify({"error": "This Plotwise needs its password.", "need_password": True}), 401
    return None


def sess(sid):
    if not re.fullmatch(r"[0-9a-f]{10}|[0-9a-f]{20}", sid) or not os.path.isdir(os.path.join(SESSIONS, sid)):
        abort(404)
    return Session(sid)


def err(e, code=400):
    return jsonify({"error": str(e)}), code


def state_payload(s):
    if not s.state.get("style"):
        s.compose_style()
    st = dict(s.state)
    cur = s.current()
    return {"sid": s.sid, "name": st.get("name") or "", "style": st["style"], "style_source": st["style_source"],
            "guide": bool(st.get("guide_style") or (st.get("style") or {}).get("guide_file")),
            "style_notes": [{k: n.get(k) for k in ("text", "learned", "via", "time")} for n in st.get("style_notes", [])],
            "style_refs": [{k: r.get(k) for k in ("id", "file", "kind", "learned", "palette", "use", "via")}
                           for r in st.get("style_refs", [])],
            "data": s.data_info() if st["data_file"] else None,
            "versions": [{k: v.get(k) for k in ("n", "request", "changes", "via", "ok", "error", "status", "totals", "kind", "github")}
                         for v in st["versions"]],
            "current": st["current"], "integrity": s.integrity() if cur else None,
            "code": open(os.path.join(cur["dir"], "code.py")).read() if cur else None,
            "log": s.read_log(), "spot_checks": st["spot_checks"], "panel": s.panel() if cur else None}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    b = llm.detect_backend()
    return jsonify({"backend": b, "backend_name": llm.BACKEND_NAMES.get(b, b), "hosted": HOSTED, "password": bool(PASSWORD)})


@app.post("/api/session")
def new_session():
    s = Session()
    s.log("App", "session", f"New session. AI backend: {llm.BACKEND_NAMES[llm.detect_backend()]}.")
    return jsonify(state_payload(s))


@app.get("/api/sessions")
def list_sessions():
    # on a shared server the browser says which sessions are its own; nobody can list the others
    ids = [x for x in request.args.get("ids", "").split(",") if re.fullmatch(r"[0-9a-f]{10}|[0-9a-f]{20}", x)]
    return jsonify({"sessions": Session.saved(only=set(ids) if HOSTED else None)})


@app.get("/api/s/<sid>")
def get_state(sid):
    return jsonify(state_payload(sess(sid)))


@app.post("/api/s/<sid>/style")
def set_style(sid):
    s = sess(sid)
    try:
        f = request.files.get("file")
        if not f:
            return err("Upload a style guide file.")
        _, note = s.set_style_doc(f.filename, f.read())
    except Exception as e:  # noqa: BLE001
        return err(e)
    return jsonify({"note": note, **state_payload(s)})


@app.post("/api/s/<sid>/refs")
def add_refs(sid):
    s = sess(sid)
    files = request.files.getlist("file")
    if not files:
        return err("Choose one or more plotting scripts or figures.")
    try:
        added = s.add_refs([(f.filename, f.read()) for f in files])
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), **state_payload(s)}), 400
    return jsonify({"note": "Read " + ", ".join(r["file"] for r in added) + ".", **state_payload(s)})


@app.post("/api/s/<sid>/data")
def set_data(sid):
    s = sess(sid)
    f = request.files.get("file")
    if not f:
        return err("No file")
    try:
        s.set_data(f.filename, f.read())
    except Exception as e:  # noqa: BLE001
        return err(f"Could not read {f.filename}: {e}")
    return jsonify(state_payload(s))


@app.post("/api/s/<sid>/<action>")
def act(sid, action):
    s = sess(sid)
    body = request.get_json(silent=True) or {}
    try:
        if action == "plot":
            s.plot(body["request"])
        elif action == "edit":
            s.edit(body["request"])
        elif action == "revert":
            s.revert(int(body["version"]))
        elif action == "format":
            s.format(body.get("settings") or {})
        elif action == "style-note":
            s.add_style_note(body.get("text", ""))
        elif action == "style-note-remove":
            s.remove_style_note(int(body.get("i", -1)))
        elif action == "ref":
            s.set_ref(str(body.get("id")), use=body.get("use"), remove=bool(body.get("remove")))
        elif action == "rename":
            s.rename(body.get("name", ""))
        elif action == "inject":
            s.inject_fault()
        elif action == "github":
            from plotwise.github import GitHubError
            try:
                res = s.deposit_github(body.get("token"), body.get("repo"), body.get("branch", ""), body.get("folder", ""),
                                       bool(body.get("include_data")))
            except GitHubError as e:
                s.log("App", "github", f"GitHub upload did not go through: {e}")
                return jsonify({"error": str(e), **state_payload(s)}), 400
            return jsonify({"github": res, **state_payload(s)})
        elif action == "spot":
            return jsonify({"points": s.spot_check(int(body.get("k", 3)))})
        elif action == "spot-answer":
            note = s.record_spot(body["answers"])
            return jsonify({"note": note, **state_payload(s)})
        else:
            abort(404)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e), **state_payload(s)}), 400
    return jsonify(state_payload(s))


@app.get("/s/<sid>/v/<int:n>/<fname>")
def version_file(sid, n, fname):
    s = sess(sid)
    if fname not in ("figure.png", "figure.pdf", "figure.svg", "inspector.html", "code.py", "integrity.json", "points.json"):
        abort(404)
    p = os.path.join(s.dir, f"v{n:03d}", fname)
    if not os.path.exists(p):
        abort(404)
    return send_file(p, max_age=0, as_attachment=request.args.get("dl") == "1",
                     download_name=f"plotwise_v{n}_{fname}")


@app.get("/s/<sid>/log.md")
def log_md(sid):
    return Response(sess(sid).log_markdown(), mimetype="text/markdown",
                    headers={"Content-Disposition": f"attachment; filename=plotwise_{sid}_ai_log.md"})


@app.get("/s/<sid>/export.zip")
def export(sid):
    s = sess(sid)
    try:
        data, name = s.export_zip()
    except ValueError as e:
        return err(e)
    return Response(data, mimetype="application/zip", headers={"Content-Disposition": f"attachment; filename={name}"})


@app.errorhandler(413)
def too_big(e):
    return jsonify({"error": f"That file is larger than {app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} MB."}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"Plotwise running on http://localhost:{port}  (AI backend: {llm.BACKEND_NAMES[llm.detect_backend()]})")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False, threaded=True)
