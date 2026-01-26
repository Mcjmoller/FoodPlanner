"""
Flask Web App for Food Planner
Provides mobile-friendly access to manage lists and run the planner.
"""
import os
import threading
import queue
import socket
from flask import Flask, render_template, request, jsonify, Response
import markdown

# Import from the existing foodPlaner module
from foodPlaner import (
    load_personal_lists,
    save_personal_lists,
    run_full_pipeline
)

app = Flask(__name__)

# Global state for background job
job_status = {
    "running": False,
    "progress": [],
    "result": None,
    "error": None
}
progress_queue = queue.Queue()


def get_local_ip():
    """Get the local IP address for mobile access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_plan():
    """Load the latest meal plan from file."""
    plan_path = "weekly_meal_plan.txt"
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            return f.read()
    return None


@app.route("/")
def index():
    """Dashboard: view latest plan and run button."""
    plan_text = load_plan()
    plan_html = markdown.markdown(plan_text, extensions=['tables', 'nl2br']) if plan_text else None
    return render_template("index.html", plan_html=plan_html, is_running=job_status["running"])


@app.route("/edit")
def edit():
    """List editor page."""
    buying, pantry = load_personal_lists()
    return render_template("edit.html", buying=buying, pantry=pantry)


@app.route("/api/lists", methods=["GET", "POST"])
def api_lists():
    """API to get/set lists as JSON."""
    if request.method == "GET":
        buying, pantry = load_personal_lists()
        return jsonify({"buying": buying, "pantry": pantry})
    else:
        data = request.get_json()
        buying = data.get("buying", [])
        pantry = data.get("pantry", [])
        save_personal_lists(buying, pantry)
        return jsonify({"status": "saved"})


def run_pipeline_thread():
    """Background thread to run the pipeline."""
    global job_status
    job_status["running"] = True
    job_status["progress"] = []
    job_status["result"] = None
    job_status["error"] = None

    def progress_cb(msg):
        job_status["progress"].append(msg)
        progress_queue.put(msg)

    try:
        result = run_full_pipeline(progress_callback=progress_cb)
        job_status["result"] = result
    except Exception as e:
        job_status["error"] = str(e)
        progress_queue.put(f"Error: {e}")
    finally:
        job_status["running"] = False
        progress_queue.put("__DONE__")


@app.route("/api/run", methods=["POST"])
def api_run():
    """Start the planning job in background."""
    if job_status["running"]:
        return jsonify({"status": "already_running"}), 400
    
    # Clear the queue
    while not progress_queue.empty():
        try:
            progress_queue.get_nowait()
        except queue.Empty:
            break
    
    thread = threading.Thread(target=run_pipeline_thread, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/status")
def api_status():
    """SSE endpoint for real-time progress."""
    def generate():
        while True:
            try:
                msg = progress_queue.get(timeout=60)
                if msg == "__DONE__":
                    yield f"data: __DONE__\n\n"
                    break
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield f"data: __KEEPALIVE__\n\n"
    
    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/plan")
def api_plan():
    """Get the current plan as JSON."""
    plan_text = load_plan()
    plan_html = markdown.markdown(plan_text, extensions=['tables', 'nl2br']) if plan_text else None
    return jsonify({"text": plan_text, "html": plan_html})


if __name__ == "__main__":
    local_ip = get_local_ip()
    print(f"\n{'='*50}")
    print(f"  Food Planner Web App")
    print(f"{'='*50}")
    print(f"  Local:   http://127.0.0.1:5000")
    print(f"  Network: http://{local_ip}:5000")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
