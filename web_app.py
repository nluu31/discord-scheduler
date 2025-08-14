import os
from flask import Flask, redirect, url_for, session, request, render_template_string, g
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import sqlite3
from datetime import datetime, timedelta

DB_FILE = "tasks.db"

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret_key')

oauth = OAuth(app)
discord = oauth.register(
    name='discord',
    client_id=os.getenv('DISCORD_CLIENT_ID'),
    client_secret=os.getenv('DISCORD_CLIENT_SECRET'),
    access_token_url='https://discord.com/api/oauth2/token',
    authorize_url='https://discord.com/api/oauth2/authorize',
    api_base_url='https://discord.com/api/',
    client_kwargs={'scope': 'identify email'},
)


def get_db_connection():
    if 'db' not in g:
        conn = sqlite3.connect(DB_FILE, timeout=5)  # Wait up to 5 sec if locked
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task TEXT NOT NULL,
            due_date TEXT NOT NULL
        );
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reminder_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            reminder_date TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
    ''')
    conn.commit()


def add_task_with_reminders(user_id, task_name, due_date_str, num_reminders):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Convert to ISO format before inserting
    due_date = datetime.strptime(due_date_str.strip(), "%b %d %Y")
    due_date_iso = due_date.strftime("%Y-%m-%d")

    cursor.execute(
        'INSERT INTO tasks (user_id, task, due_date) VALUES (?, ?, ?)',
        (user_id, task_name, due_date_iso)
    )
    task_id = cursor.lastrowid

    # Calculate reminder dates
    today = datetime.today()
    days_left = (due_date - today).days
    interval = days_left / (num_reminders + 1) if num_reminders > 0 else 0

    for i in range(1, num_reminders + 1):
        reminder_day = today + timedelta(days=round(interval * i))
        reminder_date_str = reminder_day.strftime("%Y-%m-%d")
        cursor.execute(
            'INSERT INTO reminder_dates (task_id, reminder_date) VALUES (?, ?)',
            (task_id, reminder_date_str)
        )
    conn.commit()


@app.route('/')
def home():
    user = session.get('user')
    if user:
        return redirect('/dashboard')
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
    </head>
    <body class="login">
        <div class="login-box">
            <h1>Welcome to TaskBoard</h1>
            <p>Please log in<br>with your Discord</p>
            <a href="/login" class="login-button">Login with Discord</a>
        </div>
    </body>
    </html>
    ''')




@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return discord.authorize_redirect(redirect_uri)


@app.route('/callback')
def authorize():
    token = discord.authorize_access_token()
    resp = discord.get('users/@me', token=token)
    user_info = resp.json()
    session['user'] = user_info
    return redirect('/dashboard')


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    user = session.get('user')
    if not user:
        return redirect('/')

    user_id = user['id']
    error_msg = None

    if request.method == 'POST':
        task_name = request.form.get('task').strip()
        due_date = request.form.get('due_date')
        reminders_str = request.form.get('reminders', '1')

        # Validate inputs
        try:
            num_reminders = int(reminders_str)
            if num_reminders < 1:
                raise ValueError("Number of reminders must be at least 1.")
            # Check due_date format
            datetime.strptime(due_date, "%b %d %Y")
        except Exception:
            error_msg = "Invalid input. Please check your due date format (e.g., Jul 31 2025) and reminders (positive integer)."
        else:
            add_task_with_reminders(user_id, task_name, due_date, num_reminders)

    conn = get_db_connection()
    tasks = conn.execute(
        'SELECT tasks.id, tasks.task, tasks.due_date, COUNT(reminder_dates.id) AS reminders '
        'FROM tasks LEFT JOIN reminder_dates ON tasks.id = reminder_dates.task_id '
        'WHERE tasks.user_id = ? '
        'GROUP BY tasks.id '
        'ORDER BY tasks.id',
        (user_id,)
    ).fetchall()

    sortedTasks = sorted(tasks, key = lambda x : x['due_date'])
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
    <h1>Welcome {{ user['username'] }}</h1>

    {% if error_msg %}
        <p class="error">{{ error_msg }}</p>
    {% endif %}

    <form method="POST" class="task-form">
        <input type="text" name="task" placeholder="Task name" required>
        <input type="text" name="due_date" placeholder="Due date (e.g., Aug 31 2025)" required>
        <input type="number" name="reminders" placeholder="#" min="1" value="1">
        <input type="submit" value="Add">
    </form>

    <button class="toggle-btn" onclick="toggleTasks()" id="toggle-btn">Show Tasks ▼</button>

    <ul class="task-list" id="task-list">
        {% for task in sortedTasks %}
            <li>
                <strong>{{ task['task'] }}</strong><br>
                Due: {{ task['due_date'] }} | {{ task['reminders'] }} reminder(s)
                <form method="POST" action="/delete_task" style="display:inline;">
                    <input type="hidden" name="task_id" value="{{ task['id'] }}">
                    <button type="submit">Delete</button>
                </form>
                <form method="GET" action="/edit_task/{{ task['id'] }}" style="display:inline;">
                    <button type="submit">Edit</button>
                </form>
            </li>
        {% else %}
            <p>No tasks yet.</p>
        {% endfor %}
    </ul>

    <a href="/logout" class="logout">logout</a>

    <script>
       function toggleTasks() {
        const list = document.getElementById('task-list');
        const btn = document.getElementById('toggle-btn');
        const body = document.body;
        
        list.classList.toggle('show');
        body.classList.toggle('tasks-shown');
        
        if (list.classList.contains('show')) {
            btn.textContent = "Hide Tasks ▲";
        } else {
            btn.textContent = "Show Tasks ▼";
        }
    }
    </script>
</body>
</html>

 ''', user=user, sortedTasks=sortedTasks, error_msg=error_msg)


@app.route('/delete_task', methods=['POST'])
def delete_task():
    user = session.get('user')
    if not user:
        return redirect('/')

    task_id = request.form.get('task_id')
    conn = get_db_connection()
    conn.execute('DELETE FROM tasks WHERE id = ? AND user_id = ?', (task_id, user['id']))
    conn.commit()
    return redirect('/dashboard')


@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    user = session.get('user')
    if not user:
        return redirect('/')

    conn = get_db_connection()

    if request.method == 'POST':
        new_task = request.form.get('task')
        new_due_date = request.form.get('due_date')
        new_reminders_str = request.form.get('reminders')

        error_msg = None
        try:
            new_reminders = int(new_reminders_str)
            if new_reminders < 1:
                raise ValueError("Reminders must be at least 1.")
            datetime.strptime(new_due_date, "%b %d %Y")
        except Exception:
            error_msg = "Invalid input. Please check your due date format (e.g., Jul 31 2025) and reminders (positive integer)."
            task = conn.execute(
                'SELECT * FROM tasks WHERE id = ? AND user_id = ?',
                (task_id, user['id'])
            ).fetchone()
            return render_template_string('''
                <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
                <h2>Edit Task</h2>
                <p style="color: red; font-weight: bold;">{{ error_msg }}</p>
                <form method="POST">
                    <label>Task:</label><br>
                    <input name="task" value="{{ task['task'] }}" required><br><br>
                    <label>Due date (e.g., Jul 31 2025):</label><br>
                    <input name="due_date" value="{{ task['due_date'] }}" required><br><br>
                    <label>Reminders:</label><br>
                    <input type="number" name="reminders" value="{{ task['reminders'] }}" min="1"><br><br>
                    <input type="submit" value="Update Task">
                </form>
                <br>
                <a href="/dashboard">← Back to Dashboard</a>
            ''', task=task, error_msg=error_msg)

        # No error, update task
        conn.execute(
            'UPDATE tasks SET task = ?, due_date = ? WHERE id = ? AND user_id = ?',
            (new_task, new_due_date, task_id, user['id'])
        )
        conn.execute('DELETE FROM reminder_dates WHERE task_id = ?', (task_id,))

        # Recalculate reminders
        due_date_dt = datetime.strptime(new_due_date, "%b %d %Y")
        today = datetime.today()
        days_left = (due_date_dt - today).days
        interval = days_left / (new_reminders + 1) if new_reminders > 0 else 0
        reminder_dates = []
        for i in range(1, new_reminders + 1):
            reminder_day = today + timedelta(days=round(interval * i))
            reminder_dates.append(reminder_day.strftime("%Y-%m-%d"))

        cursor = conn.cursor()
        for rd in reminder_dates:
            cursor.execute(
                'INSERT INTO reminder_dates (task_id, reminder_date) VALUES (?, ?)',
                (task_id, rd)
            )
        conn.commit()
        return redirect('/dashboard')

    task = conn.execute(
        'SELECT * FROM tasks WHERE id = ? AND user_id = ?',
        (task_id, user['id'])
    ).fetchone()

    if not task:
        return "Task not found", 404

    return render_template_string('''
        <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
        <h2>Edit Task</h2>
        <form method="POST">
            <label>Task:</label><br>
            <input name="task" value="{{ task['task'] }}" required><br><br>
            <label>Due date (e.g., Jul 31 2025):</label><br>
            <input name="due_date" value="{{ task['due_date'] }}" required><br><br>
            <label>Reminders:</label><br>
            <input type="number" name="reminders" value="{{ task['reminders'] if 'reminders' in task.keys() else 1 }}" min="1"><br><br>
            <input type="submit" value="Update Task">
        </form>
        <br>
        <a href="/dashboard">← Back to Dashboard</a>
    ''', task=task)


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')


if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True)
