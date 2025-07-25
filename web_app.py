import os
import json
from flask import Flask, redirect, url_for, session, request, render_template_string
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import sqlite3

DB_FILE = "tasks.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task TEXT NOT NULL,
            due_date TEXT NOT NULL,
            reminders INTEGER NOT NULL
        );
    ''')
    conn.commit()
    conn.close()


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

# Simple homepage
@app.route('/')
def home():
    user = session.get('user')
    if user:
        return (
            f"<h1>Welcome, {user['username']}#{user['discriminator']}!</h1>"
            f"<p>Your Discord ID is: {user['id']}</p>"
            f"<a href='/dashboard'>Go to dashboard</a><br>"
            f"<a href='/logout'>Logout</a>"
        )
    return '<a href="/login">Login with Discord</a>'

# Start OAuth login
@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return discord.authorize_redirect(redirect_uri)

# OAuth callback handler
@app.route('/callback')
def authorize():
    token = discord.authorize_access_token()
    resp = discord.get('users/@me', token=token)
    user_info = resp.json()
    session['user'] = user_info
    return redirect('/dashboard')

# Protected dashboard
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    user = session.get('user')
    if not user:
        return redirect('/')

    user_id = user['id']
    conn = get_db_connection()

    if request.method == 'POST':
        task_name = request.form.get('task')
        due_date = request.form.get('due_date')  # Format: Jul 31 2025
        reminders = int(request.form.get('reminders', 1))

        conn.execute(
            'INSERT INTO tasks (user_id, task, due_date, reminders) VALUES (?, ?, ?, ?)',
            (user_id, task_name, due_date, reminders)
        )
        conn.commit()

    tasks = conn.execute(
        'SELECT * FROM tasks WHERE user_id = ?',
        (user_id,)
    ).fetchall()
    conn.close()

    return render_template_string('''
        <h2>Dashboard for {{ user['username'] }}#{{ user['discriminator'] }}</h2>
        <p>Discord ID: {{ user['id'] }}</p>
        <form method="POST">
            <label>Task:</label><br>
            <input name="task" required><br><br>
            <label>Due date (e.g., Jul 31 2025):</label><br>
            <input name="due_date" required><br><br>
            <label>Reminders:</label><br>
            <input type="number" name="reminders" value="1" min="1"><br><br>
            <input type="submit" value="Add Task">
        </form>
        <hr>
        <h3>Scheduled Tasks:</h3>
{% if tasks %}
    <ul>
        {% for task in tasks %}
            <li>
                <b>{{ task['task'] }}</b> — due {{ task['due_date'] }} — {{ task['reminders'] }} reminder(s)
                <form method="POST" action="/delete_task" style="display:inline;">
                    <input type="hidden" name="task_id" value="{{ task['id'] }}">
                    <button type="submit">Delete</button>
                </form>
                <form method="GET" action="/edit_task/{{ task['id'] }}" style="display:inline;">
                    <button type="submit">Edit</button>
                </form>
            </li>
        {% endfor %}
    </ul>
{% else %}
    <p>No tasks yet.</p>
{% endif %}

        <br>
        <a href="/logout">Logout</a>
    ''', user=user, tasks=tasks)


@app.route('/delete_task', methods=['POST'])
def delete_task():
    user = session.get('user')
    if not user:
        return redirect('/')

    task_id = request.form.get('task_id')

    conn = get_db_connection()
    conn.execute('DELETE FROM tasks WHERE id = ? AND user_id = ?', (task_id, user['id']))
    conn.commit()
    conn.close()

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
        new_reminders = int(request.form.get('reminders'))

        conn.execute(
            'UPDATE tasks SET task = ?, due_date = ?, reminders = ? WHERE id = ? AND user_id = ?',
            (new_task, new_due_date, new_reminders, task_id, user['id'])
        )
        conn.commit()
        conn.close()
        return redirect('/dashboard')

    task = conn.execute(
        'SELECT * FROM tasks WHERE id = ? AND user_id = ?',
        (task_id, user['id'])
    ).fetchone()
    conn.close()

    if not task:
        return "Task not found", 404

    return render_template_string('''
        <h2>Edit Task</h2>
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
    ''', task=task)



@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

