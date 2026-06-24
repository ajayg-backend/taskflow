import sqlite3
import csv
import random
import os
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, make_response, g, jsonify, Response
from werkzeug.utils import secure_filename
from typing import List, Any, Optional, Dict

app: Flask = Flask(__name__)
DATABASE: str = "lifeos.db"
UPLOAD_FOLDER = 'static/uploads/memories'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db() -> sqlite3.Connection:
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exception: Optional[BaseException]) -> None:
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def patch_database() -> None:
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, category TEXT, date TEXT)")
    conn.commit()
    
    columns_to_add = [
        ("media_path", "TEXT DEFAULT ''"),
        ("media_type", "TEXT DEFAULT ''"),
        ("is_hidden", "INTEGER DEFAULT 0")
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
            
    conn.close()

patch_database()

with app.app_context():
    db: sqlite3.Connection = get_db()
    cursor: sqlite3.Cursor = db.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT,
        completed INTEGER DEFAULT 0,
        time TEXT,
        priority TEXT,
        notes TEXT,
        category TEXT,
        date TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        current_streak INTEGER DEFAULT 0,
        best_streak INTEGER DEFAULT 0,
        last_completed_date TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        streak INTEGER DEFAULT 0,
        best_streak INTEGER DEFAULT 0,
        last_done_date TEXT DEFAULT ''
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habit_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        habit_id INTEGER,
        date TEXT,
        FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        progress INTEGER DEFAULT 0,
        deadline TEXT DEFAULT '',
        is_completed INTEGER DEFAULT 0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pomodoro_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        minutes INTEGER DEFAULT 0
    )
    """)
    db.commit()

def update_streak_logic() -> None:
    db: sqlite3.Connection = get_db()
    cursor: sqlite3.Cursor = db.cursor()
    cursor.execute("SELECT current_streak, best_streak, last_completed_date FROM streaks WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO streaks (current_streak, best_streak, last_completed_date) VALUES (1, 1, ?)", (datetime.now().strftime("%Y-%m-%d"),))
        db.commit()
        return
    current, best, last_date = row
    
    today_str: str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str: str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    if last_date == today_str:
        return
    if last_date == yesterday_str:
        current += 1
    else:
        current = 1
        
    if current > best:
        best = current
        
    cursor.execute("UPDATE streaks SET current_streak=?, best_streak=?, last_completed_date=? WHERE id = 1", (int(current), int(best), str(today_str)))
    db.commit()

@app.route("/")
def home() -> Any:
    return redirect("/planner")

@app.route("/planner", methods=["GET", "POST"])
def planner() -> Any:
    db: sqlite3.Connection = get_db()
    cursor: sqlite3.Cursor = db.cursor()
    today_str: str = datetime.now().strftime("%Y-%m-%d")
    
    view_date: str = request.args.get('date', today_str)
    
    if request.method == "POST" and "task" in request.form:
        task: str = request.form["task"]
        time: str = request.form["time"]
        priority: str = request.form["priority"]
        notes: str = request.form.get("notes", "")
        category: str = request.form.get("category", "General")
        task_date: str = request.form.get("date", view_date)

        cursor.execute("INSERT INTO tasks (task, time, priority, notes, category, date) VALUES (?, ?, ?, ?, ?, ?)", (task, time, priority, notes, category, task_date))
        db.commit()

    # 🎯 EXACT CHANGE DONE HERE: Order modified to display recently completed/created tasks first
    cursor.execute("SELECT id, task, completed, time, priority, notes, category, IFNULL(date, '') FROM tasks WHERE date = ? ORDER BY completed ASC, id DESC", (view_date,))
    tasks: List[Any] = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM tasks WHERE date = ?", (view_date,))
    total_tasks = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE completed = 1 AND date = ?", (view_date,))
    completed_tasks = cursor.fetchone()[0] or 0
    pending_tasks: int = total_tasks - completed_tasks
    productivity_score: int = int((completed_tasks / total_tasks) * 100) if total_tasks > 0 else 0

    cursor.execute("SELECT current_streak, best_streak FROM streaks WHERE id = 1")
    streak_data = cursor.fetchone()
    current_streak: int = streak_data[0] if streak_data else 0
    best_streak: int = streak_data[1] if streak_data else 0

    cursor.execute("SELECT id, name, streak, best_streak, last_done_date FROM habits")
    raw_habits = cursor.fetchall()
    habits: List[Dict[str, Any]] = []
    
    for h in raw_habits:
        h_id, h_name, h_streak, h_best, h_last_date = h
        cursor.execute("SELECT COUNT(*) FROM habit_history WHERE habit_id = ?", (h_id,))
        total_logs = cursor.fetchone()[0]
        completion_pct = min(100, int((total_logs / 30) * 100)) if total_logs > 0 else 0
        
        cursor.execute("SELECT COUNT(*) FROM habit_history WHERE habit_id = ? AND date = ?", (h_id, view_date))
        is_done_on_date = cursor.fetchone()[0] > 0

        habits.append({
            "id": h_id, "name": h_name, "streak": h_streak, "best_streak": h_best,
            "last_done_date": h_last_date, "completion_pct": completion_pct, "is_done_on_date": is_done_on_date
        })
    
    cursor.execute("SELECT id, title, progress, deadline, is_completed FROM goals")
    goals: List[Any] = cursor.fetchall()

    cursor.execute("SELECT id, content, category, date, media_path, media_type, is_hidden FROM memories ORDER BY date DESC, id DESC")
    memories: List[Any] = cursor.fetchall()

    cursor.execute("SELECT SUM(minutes) FROM pomodoro_stats WHERE date = ?", (view_date,))
    today_focus_row = cursor.fetchone()
    today_focus_mins: int = today_focus_row[0] if today_focus_row and today_focus_row[0] else 0
    focus_hours: int = today_focus_mins // 60
    focus_rem_mins: int = today_focus_mins % 60
    focus_time_text: str = f"{focus_hours}h {focus_rem_mins}m" if focus_hours > 0 else f"{focus_rem_mins}m"

    cursor.execute("SELECT SUM(minutes) FROM pomodoro_stats")
    total_focus_hours: float = round((cursor.fetchone()[0] or 0) / 60, 1)

    weekly_focus_stats: List[Dict[str, Any]] = []
    graph_days: List[str] = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    for d in graph_days:
        cursor.execute("SELECT SUM(minutes) FROM pomodoro_stats WHERE date = ?", (d,))
        day_mins_val = cursor.fetchone()[0] or 0
        weekly_focus_stats.append({"date": d[-5:], "mins": day_mins_val, "height": min(60, int(day_mins_val * 0.5))})

    morning_quotes = ["Good morning, Ajay! Opportunities don't happen, you create them. ☀️"]
    afternoon_quotes = ["Good afternoon, Ajay! Small daily improvements lead to stunning results. 🌤️"]
    evening_quotes = ["Good evening, Ajay! Review your day, celebrate your wins, and rest well. 🌙"]

    current_hour = datetime.now().hour
    if current_hour < 12: greeting = random.choice(morning_quotes)
    elif current_hour < 17: greeting = random.choice(afternoon_quotes)
    else: greeting = random.choice(evening_quotes)

    achievements: List[str] = []
    if completed_tasks >= 1: achievements.append("🏅 First Task Completed")
    if current_streak >= 3: achievements.append("🔥 3-Day Win Streak")

    current_time_str: str = datetime.now().strftime("%H:%M")

    return render_template(
        "planner.html", 
        tasks=tasks, total_tasks=total_tasks, pending_tasks=pending_tasks, completed_tasks=completed_tasks,
        productivity_score=productivity_score, current_streak=current_streak, best_streak=best_streak,
        current_time_str=current_time_str, today_date=view_date, real_today=today_str,
        habits=habits, goals=goals, achievements=achievements,
        today_focus_mins=today_focus_mins, focus_time_text=focus_time_text,
        total_focus_hours=total_focus_hours, weekly_focus_stats=weekly_focus_stats,
        last_7_days=[d[-5:] for d in graph_days], greeting=greeting, memories=memories
    )

@app.route("/add_memory", methods=["POST"])
def add_memory() -> Any:
    content: Optional[str] = request.form.get("content")
    category: str = request.form.get("category", "Daily Reflection")
    m_date: str = request.form.get("date", datetime.now().strftime("%Y-%m-%d"))
    
    media_path = ""
    media_type = ""
    
    file = request.files.get("media_file")
    if file and file.filename != "":
        filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        media_path = f"/static/uploads/memories/{filename}"
        
        ext = filename.split('.')[-1].lower()
        if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp']: media_type = 'image'
        elif ext in ['mp4', 'mov', 'avi', 'webm']: media_type = 'video'
        elif ext in ['mp3', 'wav', 'ogg', 'm4a']: media_type = 'audio'

    if content:
        db: sqlite3.Connection = get_db()
        db.execute("INSERT INTO memories (content, category, date, media_path, media_type, is_hidden) VALUES (?, ?, ?, ?, ?, 0)", (content, category, m_date, media_path, media_type))
        db.commit()
    return redirect(f"/planner?date={m_date}")

@app.route("/toggle_memory_hide/<int:id>", methods=["POST"])
def toggle_memory_hide(id: int) -> Any:
    db: sqlite3.Connection = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT is_hidden FROM memories WHERE id=?", (id,))
    row = cursor.fetchone()
    if row:
        new_status = 1 if row[0] == 0 else 0
        db.execute("UPDATE memories SET is_hidden=? WHERE id=?", (new_status, id))
        db.commit()
    return jsonify({"status": "success"})

@app.route("/delete_memory/<int:id>")
def delete_memory(id: int) -> Any:
    target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    db: sqlite3.Connection = get_db()
    db.execute("DELETE FROM memories WHERE id=?", (id,))
    db.commit()
    return redirect(f"/planner?date={target_date}")

@app.route("/api/quick_complete/<int:id>", methods=["POST"])
def quick_complete(id: int) -> Any:
    db: sqlite3.Connection = get_db()
    db.execute("UPDATE tasks SET completed=1 WHERE id=?", (id,))
    db.commit()
    update_streak_logic()
    return jsonify({"status": "success"})

@app.route("/api/snooze_task/<int:id>", methods=["POST"])
def snooze_task(id: int) -> Any:
    req_json = request.json or {}
    minutes = int(req_json.get("minutes", 10))
    new_time = (datetime.now() + timedelta(minutes=minutes)).strftime("%H:%M")
    db: sqlite3.Connection = get_db()
    db.execute("UPDATE tasks SET time=? WHERE id=?", (new_time, id))
    db.commit()
    return jsonify({"status": "success", "new_time": new_time})

@app.route("/add_habit", methods=["POST"])
def add_habit() -> Any:
    name: Optional[str] = request.form.get("name")
    redirect_date = request.form.get("date", datetime.now().strftime("%Y-%m-%d"))
    if name:
        db: sqlite3.Connection = get_db()
        db.execute("INSERT INTO habits (name, streak, best_streak, last_done_date) VALUES (?, 0, 0, '')", (name,))
        db.commit()
    return redirect(f"/planner?date={redirect_date}")

@app.route("/check_habit/<int:id>")
def check_habit(id: int) -> Any:
    db: sqlite3.Connection = get_db()
    cursor: sqlite3.Cursor = db.cursor()
    
    target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    yesterday_str: str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    cursor.execute("SELECT count(*) FROM habit_history WHERE habit_id=? AND date=?", (id, target_date))
    already_done = cursor.fetchone()[0] > 0
    
    if not already_done:
        cursor.execute("SELECT streak, best_streak, last_done_date FROM habits WHERE id=?", (id,))
        habit = cursor.fetchone()
        if habit:
            streak, best, last_date = habit
            if last_date == yesterday_str or last_date == "": 
                streak += 1
            else: 
                streak = 1
            if streak > best: best = streak
            
            db.execute("UPDATE habits SET streak=?, best_streak=?, last_done_date=? WHERE id=?", (int(streak), int(best), str(target_date), id))
            db.execute("INSERT INTO habit_history (habit_id, date) VALUES (?, ?)", (id, target_date))
            db.commit()
            
    return redirect(f"/planner?date={target_date}")

@app.route("/delete_habit/<int:id>")
def delete_habit(id: int) -> Any:
    target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    db: sqlite3.Connection = get_db()
    db.execute("DELETE FROM habits WHERE id=?", (id,))
    db.execute("DELETE FROM habit_history WHERE habit_id=?", (id,))
    db.commit()
    return redirect(f"/planner?date={target_date}")

@app.route("/add_goal", methods=["POST"])
def add_goal() -> Any:
    title: Optional[str] = request.form.get("title")
    deadline: str = request.form.get("deadline", "")
    redirect_date = request.form.get("date", datetime.now().strftime("%Y-%m-%d"))
    if title:
        db: sqlite3.Connection = get_db()
        db.execute("INSERT INTO goals (title, progress, deadline, is_completed) VALUES (?, 0, ?, 0)", (title, deadline))
        db.commit()
    return redirect(f"/planner?date={redirect_date}")

@app.route("/update_goal/<int:id>", methods=["POST"])
def update_goal(id: int) -> Any:
    progress: Optional[int] = request.form.get("progress", type=int)
    redirect_date = request.form.get("date", datetime.now().strftime("%Y-%m-%d"))
    if progress is not None:
        db: sqlite3.Connection = get_db()
        is_comp = 1 if progress >= 100 else 0
        db.execute("UPDATE goals SET progress=?, is_completed=? WHERE id=?", (progress, is_comp, id))
        db.commit()
    return redirect(f"/planner?date={redirect_date}")

@app.route("/delete_goal/<int:id>")
def delete_goal(id: int) -> Any:
    target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    db: sqlite3.Connection = get_db()
    db.execute("DELETE FROM goals WHERE id=?", (id,))
    db.commit()
    return redirect(f"/planner?date={target_date}")

@app.route("/save_pomodoro", methods=["POST"])
def save_pomodoro() -> Any:
    req_json = request.json
    minutes: int = req_json.get("minutes", 0) if req_json else 0
    today_str: str = datetime.now().strftime("%Y-%m-%d")
    db: sqlite3.Connection = get_db()
    db.execute("INSERT INTO pomodoro_stats (date, minutes) VALUES (?, ?)", (today_str, minutes))
    db.commit()
    return jsonify({"status": "success"})

@app.route("/complete/<int:id>")
def complete(id: int) -> Any:
    target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    db: sqlite3.Connection = get_db()
    db.execute("UPDATE tasks SET completed=1 WHERE id=?", (id,))
    db.commit()
    update_streak_logic()
    return redirect(f"/planner?date={target_date}")

@app.route("/delete/<int:id>")
def delete(id: int) -> Any:
    target_date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    db: sqlite3.Connection = get_db()
    db.execute("DELETE FROM tasks WHERE id=?", (id,))
    db.commit()
    return redirect(f"/planner?date={target_date}")

@app.route("/export")
def export_tasks() -> Response:
    db: sqlite3.Connection = get_db()
    cursor: sqlite3.Cursor = db.cursor()
    cursor.execute("SELECT task, completed, time, priority, notes, category, date FROM tasks")
    rows = cursor.fetchall()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["task", "completed", "time", "priority", "notes", "category", "date"])
    cw.writerows(rows)
    response = make_response(si.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename=lifeos_backup_{datetime.now().strftime('%Y%m%d')}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

if __name__ == "__main__":
    app.run(debug=True)