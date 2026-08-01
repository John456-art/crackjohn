import os
import sys
import io
import random
import string
import subprocess
import sqlite3
import json
import queue
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, send_from_directory

# 🟢 تنظیم انکودینگ سرور روی UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

app = Flask(__name__)
app.secret_key = "super-secret-key-change-this"

# ---------------------------------------------------------
# پیکربندی مسیرها و ساختار دیتابیس
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_FILES_DIR = os.path.join(BASE_DIR, "user_files")
DB_PATH = os.path.join(BASE_DIR, "database.db")
ADMIN_KEY = "1231231412312314"

os.makedirs(USER_FILES_DIR, exist_ok=True)

active_sessions = {}

def safe_join(base_dir, path):
    """جلوگیری از حملات دسترسی به مسیرهای نامعتبر (Path Traversal)"""
    if not path:
        return base_dir
    cleaned_path = os.path.normpath(path).lstrip("/\\")
    target_path = os.path.abspath(os.path.join(base_dir, cleaned_path))
    base_abs = os.path.abspath(base_dir)
    if not target_path.startswith(base_abs):
        return None
    return target_path

def ensure_user_environment(user_code):
    """ایجاد پوشه اختصاصی کاربر و ساخت فایل run.py تعاملی نمونه"""
    user_folder = os.path.join(USER_FILES_DIR, user_code)
    os.makedirs(user_folder, exist_ok=True)
    
    xbox_dir = os.path.join(user_folder, "xbox_results")
    os.makedirs(xbox_dir, exist_ok=True)
    
    user_script_path = os.path.join(user_folder, "run.py")
    
    if not os.path.exists(user_script_path):
        default_python_code = '''# === فایل پایتون تعاملی کاربر ===
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("--- سیستم تعاملی پردازش پایتون ---")

# نمونه سوال تعاملی از کاربر
name = input("لطفاً نام خود را وارد کنید: ")
print(f"سلام {name} عزیز! پردازش فایل‌های شما آغاز شد.\\n")

txt_files = [f for f in os.listdir('.') if f.endswith('.txt')]

if not txt_files:
    print("هیچ فایل txt در پوشه شما یافت نشد. لطفاً ابتدا فایل آپلود کنید.")
else:
    for file_name in txt_files:
        print(f"در حال خواندن فایل: {file_name}")
        with open(file_name, 'r', encoding='utf-8', errors='replace') as f:
            print(f"محتوا:\\n{f.read()}")
            print("-" * 30)

print("--- پردازش با موفقیت پایان یافت ---")
'''
        with open(user_script_path, "w", encoding="utf-8") as f:
            f.write(default_python_code)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_text TEXT UNIQUE NOT NULL,
                user_code TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        try:
            conn.execute("ALTER TABLE keys ADD COLUMN is_active INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_code TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

init_db()

def generate_random_key(length=20):
    return ''.join(random.choices(string.digits, k=length))

def enqueue_output(proc, q, log_acc):
    """خواندن کاراکتر به‌کاراکتر خروجی پایتون جهت پشتیبانی از input()"""
    while True:
        char = proc.stdout.read(1)
        if not char:
            break
        q.put(char)
        log_acc.append(char)
    proc.stdout.close()
    proc.wait()
    q.put(None)

# ---------------------------------------------------------
# روت‌های اصلی و کاربری
# ---------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        key_input = request.form.get("access_key", "").strip()
        
        if key_input == ADMIN_KEY:
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))
        
        with get_db() as conn:
            user = conn.execute("SELECT * FROM keys WHERE key_text = ?", (key_input,)).fetchone()
            
            if user:
                if user["is_active"] == 0:
                    flash("حساب کاربری شما توسط مدیریت غیرفعال شده است.", "danger")
                    return render_template("login.html")
                
                expires_at = datetime.strptime(user["expires_at"], "%Y-%m-%d %H:%M:%S")
                if datetime.now() > expires_at:
                    flash("اعتبار کلید لایسنس شما به پایان رسیده است.", "danger")
                else:
                    session["user_code"] = user["user_code"]
                    session["key_text"] = user["key_text"]
                    session["expires_at"] = user["expires_at"]
                    
                    ensure_user_environment(user["user_code"])
                    return redirect(url_for("dashboard"))
            else:
                flash("کلید وارد شده معتبر نمی‌باشد.", "danger")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user_code" not in session:
        return redirect(url_for("login"))
    
    user_code = session["user_code"]
    user_folder = os.path.join(USER_FILES_DIR, user_code)
    ensure_user_environment(user_code)
    
    files = os.listdir(user_folder)
    txt_files = [f for f in files if os.path.isfile(os.path.join(user_folder, f)) and f.endswith('.txt')]

    return render_template(
        "dashboard.html",
        user_code=user_code,
        files=txt_files,
        expires_at=session.get("expires_at")
    )

@app.route("/upload", methods=["POST"])
def upload_file():
    if "user_code" not in session:
        return redirect(url_for("login"))
    
    user_code = session["user_code"]
    
    with get_db() as conn:
        user = conn.execute("SELECT is_active FROM keys WHERE user_code = ?", (user_code,)).fetchone()
        if not user or user["is_active"] == 0:
            flash("حساب کاربری شما غیرفعال شده است.", "danger")
            return redirect(url_for("login"))

    if "txt_file" not in request.files:
        flash("فایلی انتخاب نشده است.", "warning")
        return redirect(url_for("dashboard"))
    
    file = request.files["txt_file"]
    if file.filename == "" or not file.filename.endswith(".txt"):
        flash("کمبو رو در محل چکر وارد کنید", "danger")
        return redirect(url_for("dashboard"))
    
    user_folder = os.path.join(USER_FILES_DIR, user_code)
    file.save(os.path.join(user_folder, file.filename))
    flash("فایل txt با موفقیت آپلود شد.", "success")
        
    return redirect(url_for("dashboard"))

# ---------------------------------------------------------
# روت‌های مدیریت فولدرها و دانلود فایل‌های xbox_results
# ---------------------------------------------------------
@app.route("/get-xbox-results", methods=["GET"])
def get_xbox_results():
    """دریافت ساختار پوشه‌ها و فایل‌های درون xbox_results"""
    if "user_code" not in session:
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    user_code = session["user_code"]
    xbox_dir = os.path.join(USER_FILES_DIR, user_code, "xbox_results")
    subpath = request.args.get("subpath", "").strip()
    
    target_dir = safe_join(xbox_dir, subpath)
    if not target_dir or not os.path.exists(target_dir):
        return jsonify({"success": False, "message": "مسیر مورد نظر یافت نشد."}), 404

    dirs = []
    files = []
    
    try:
        for item in os.listdir(target_dir):
            item_path = os.path.join(target_dir, item)
            if os.path.isdir(item_path):
                dirs.append(item)
            elif os.path.isfile(item_path):
                files.append(item)
                
        dirs.sort()
        files.sort()
        
        return jsonify({
            "success": True,
            "subpath": subpath,
            "dirs": dirs,
            "files": files
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/download-result/<path:filename>")
def download_result(filename):
    """دانلود یا مشاهده مستقیم فایل‌های خروجی (با پشتیبانی از زیرپوشه‌ها)"""
    if "user_code" not in session:
        return redirect(url_for("login"))
    
    user_code = session["user_code"]
    xbox_dir = os.path.join(USER_FILES_DIR, user_code, "xbox_results")
    
    target_file = safe_join(xbox_dir, filename)
    if not target_file or not os.path.isfile(target_file):
        flash("فایل مورد نظر یافت نشد.", "danger")
        return redirect(url_for("dashboard"))
    
    return send_from_directory(xbox_dir, filename, as_attachment=True)

# ---------------------------------------------------------
# روت‌های اجرا و استریم تعاملی ترمینال
# ---------------------------------------------------------
@app.route("/run-script", methods=["POST"])
def run_script():
    if "user_code" not in session:
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    user_code = session["user_code"]
    
    with get_db() as conn:
        user = conn.execute("SELECT is_active FROM keys WHERE user_code = ?", (user_code,)).fetchone()
        if not user or user["is_active"] == 0:
            return jsonify({"success": False, "message": "❌ حساب کاربری شما غیرفعال شده است."}), 403

    user_folder = os.path.join(USER_FILES_DIR, user_code)
    ensure_user_environment(user_code)
    
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "run.py"],
            cwd=user_folder,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=0
        )
        
        q = queue.Queue()
        logs = []
        
        thread = threading.Thread(target=enqueue_output, args=(proc, q, logs), daemon=True)
        thread.start()
        
        active_sessions[user_code] = {
            "proc": proc,
            "queue": q,
            "logs": logs,
            "thread": thread
        }
        
        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/stream-script")
def stream_script():
    user_code = session.get("user_code")
    if not user_code or user_code not in active_sessions:
        return "سشن معتبر یافت نشد", 400

    sess_data = active_sessions[user_code]
    q = sess_data["queue"]
    proc = sess_data["proc"]
    logs = sess_data["logs"]

    def generate():
        while True:
            try:
                chunk = q.get(timeout=0.05)
                if chunk is None:
                    break
                
                buffer = [chunk]
                while not q.empty():
                    item = q.get_nowait()
                    if item is None:
                        q.put(None)
                        break
                    buffer.append(item)
                
                full_text = "".join(buffer)
                yield f"data: {json.dumps({'text': full_text})}\n\n"
                
            except queue.Empty:
                if proc.poll() is not None and q.empty():
                    break

        full_log = "".join(logs).strip()
        status = "SUCCESS" if proc.returncode == 0 else "ERROR"
        
        with get_db() as conn:
            conn.execute(
                "INSERT INTO execution_logs (user_code, status, output) VALUES (?, ?, ?)",
                (user_code, status, full_log if full_log else "بدون خروجی متنی")
            )
            conn.commit()

        yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

@app.route("/send-input", methods=["POST"])
def send_input():
    user_code = session.get("user_code")
    if not user_code or user_code not in active_sessions:
        return jsonify({"success": False, "message": "پردازش فعالی یافت نشد."}), 400

    proc = active_sessions[user_code]["proc"]
    user_text = request.json.get("input", "")

    if proc.poll() is None:
        try:
            proc.stdin.write(user_text + "\n")
            proc.stdin.flush()
            active_sessions[user_code]["logs"].append(f"{user_text}\n")
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)})
    
    return jsonify({"success": False, "message": "پردازش خاتمه یافته است."})

# ---------------------------------------------------------
# روت‌های مدیریت و پنل ادمین
# ---------------------------------------------------------
@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if not session.get("is_admin"):
        return redirect(url_for("login"))
    
    with get_db() as conn:
        if request.method == "POST":
            count = conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
            new_code = f"code_{count + 1}"
            
            new_key = generate_random_key()
            created_at = datetime.now()
            expires_at = created_at + timedelta(days=30)
            
            conn.execute(
                "INSERT INTO keys (key_text, user_code, created_at, expires_at, is_active) VALUES (?, ?, ?, ?, 1)",
                (new_key, new_code, created_at.strftime("%Y-%m-%d %H:%M:%S"), expires_at.strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            
            ensure_user_environment(new_code)
            flash(f"کلید جدید ایجاد شد: {new_key} (شناسه: {new_code})", "success")
            return redirect(url_for("admin_panel"))

        all_keys = conn.execute("SELECT * FROM keys ORDER BY id DESC").fetchall()
        logs = conn.execute("SELECT * FROM execution_logs ORDER BY id DESC LIMIT 100").fetchall()
        
    return render_template("admin.html", keys=all_keys, logs=logs)

@app.route("/admin/toggle-user/<user_code>", methods=["POST"])
def toggle_user(user_code):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    with get_db() as conn:
        user = conn.execute("SELECT is_active FROM keys WHERE user_code = ?", (user_code,)).fetchone()
        if user:
            new_status = 0 if user["is_active"] == 1 else 1
            conn.execute("UPDATE keys SET is_active = ? WHERE user_code = ?", (new_status, user_code))
            conn.commit()
            return jsonify({"success": True, "is_active": new_status})
    
    return jsonify({"success": False, "message": "کاربر یافت نشد."})

@app.route("/admin/clear-logs", methods=["POST"])
def clear_logs():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    with get_db() as conn:
        conn.execute("DELETE FROM execution_logs")
        conn.commit()
    
    flash("تاریخچه لاگ‌ها با موفقیت پاکسازی شد.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/get-script/<user_code>", methods=["GET"])
def get_user_script(user_code):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    user_folder = os.path.join(USER_FILES_DIR, user_code)
    script_path = os.path.join(user_folder, "run.py")
    ensure_user_environment(user_code)
    
    try:
        with open(script_path, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
        return jsonify({"success": True, "code": code})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/admin/save-script/<user_code>", methods=["POST"])
def save_user_script(user_code):
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    new_code = request.json.get("code", "")
    user_folder = os.path.join(USER_FILES_DIR, user_code)
    script_path = os.path.join(user_folder, "run.py")
    
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(new_code)
        return jsonify({"success": True, "message": "کد پایتون این کاربر بروزرسانی شد."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/admin/run-terminal", methods=["POST"])
def admin_run_terminal():
    if not session.get("is_admin"):
        return jsonify({"success": False, "message": "عدم دسترسی"}), 403
    
    command = request.json.get("command", "").strip()
    if not command:
        return jsonify({"success": False, "output": "دستوری وارد نشده است."})
    
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120
        )
        output = result.stdout if result.stdout else result.stderr
        if not output.strip():
            output = "دستور با موفقیت و بدون خروجی متنی اجرا شد."
            
        return jsonify({"success": True, "output": output})
    except Exception as e:
        return jsonify({"success": False, "output": str(e)})

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True, threaded=True, port=5000)