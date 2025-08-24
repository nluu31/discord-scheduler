import os
from flask import Flask, redirect, url_for, session, request, render_template_string, g
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import sqlite3
from datetime import datetime, timedelta
import logging

DB_FILE = "tasks.db"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret_key')

logger = logging.getLogger('scheduler_webapp') # Unique name for this component
handler = logging.FileHandler('webapp.log', mode='a') # Append mode
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


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
    user = session.get('user')

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
    logger.info(f"User '{user['global_name']}' has manually added task '{task_name}' using the website.")


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
            if num_reminders > 10:
                raise ValueError("Number of reminders must cannot exceed 10.")
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
    
    # Generate bot invite URL with your bot's client ID and required permissions
    bot_client_id = os.getenv('DISCORD_BOT_CLIENT_ID', os.getenv('DISCORD_CLIENT_ID'))  # Fallback to OAuth client ID if bot ID not set
    bot_invite_url = f"https://discord.com/api/oauth2/authorize?client_id={bot_client_id}&permissions=2048&scope=bot"
    
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
    <!-- Bot Invite Icon -->
    <div class="bot-invite-icon" onclick="showBotInvitePopup()" title="Enable Discord Notifications"></div>

    <!-- Bot Invite Popup -->
    <div class="popup-overlay" id="botInvitePopup" onclick="hideBotInvitePopup(event)">
        <div class="popup-content" onclick="event.stopPropagation()">
            <div class="warning-icon">⚠️</div>
            <h2 class="popup-header">Enable Discord Notifications</h2>
            <div class="popup-text">
                <p><strong>Want to receive task reminders directly in Discord?</strong></p>
                <p>To send you notifications, our bot needs to be in a server that you're also in. Here's what you can do:</p>
                <ul class="feature-list">
                    <li>✅ Invite the bot to your personal server</li>
                    <li>✅ Ask a server admin to invite the bot</li>
                    <li>✅ Join a server that already has the bot</li>
                </ul>
                <p><em>Don't worry - this is completely optional! You can still use TaskBoard without Discord notifications.</em></p>
            </div>
            <div class="popup-buttons">
                <a href="{{ bot_invite_url }}" class="invite-btn" target="_blank">
                    🤖 Invite Bot to Server
                </a>
                <button class="close-btn" onclick="hideBotInvitePopup()">Maybe Later</button>
            </div>
        </div>
    </div>
     
    <h1>Please Enter your Tasks:  </h1>

    {% if error_msg %}
        <p class="error">{{ error_msg }}</p>
    {% endif %}

    <form method="POST" class="task-form">
        <input type="text" name="task" placeholder="Task name" required>
        <input type="text" name="due_date" placeholder="Due date (e.g., Aug 31 2025)" required>
        <input type="number" name="reminders" placeholder="#" min="1" value="1">
        <input type="submit" value="Add">
    </form>

<div class ="dropdown-container">
    <button class="toggle-btn" onclick="toggleTasks()" id="toggle-btn">Show Tasks ▼</button>

    <ul class="task-list" id="task-list">
        {% for task in sortedTasks %}
            <li>
    <div class="task-content">
        <strong>{{ task['task'] }}</strong>
        <div class="task-meta">
            <span>Due: {{ datetime.strptime(task['due_date'], '%Y-%m-%d').strftime('%A, %B %d, %Y') }}</span>
            <span class="reminder-date">{{ task['reminders'] }} reminder(s)</span>
        </div>
    </div>
    <div class="task-actions">
    <button class="delete-btn" data-task-id="{{ task['id'] }}">Delete</button>
    <form method="GET" action="/edit_task/{{ task['id'] }}" style="display:inline;">
        <button type="submit" class="edit-btn">Edit</button>
    </form>
</div>
</li>
        {% else %}
            <p>No tasks yet.</p>
        {% endfor %}
    </ul>


    
    <a href="/logout" class="logout">logout {{ user['username'] }}</a>

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

    // Bot Invite Popup Functions
    function showBotInvitePopup() {
        const popup = document.getElementById('botInvitePopup');
        popup.classList.add('show');
        document.body.style.overflow = 'hidden'; // Prevent background scrolling
    }

    function hideBotInvitePopup(event) {
        // Only close if clicking overlay or close button, not the popup content
        if (!event || event.target === event.currentTarget || event.target.classList.contains('close-btn')) {
            const popup = document.getElementById('botInvitePopup');
            popup.classList.remove('show');
            document.body.style.overflow = ''; // Restore scrolling
        }
    }

    // Close popup with Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            hideBotInvitePopup();
        }
    });

    document.addEventListener('DOMContentLoaded', function() {
    // Handle delete clicks
    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', async function(e) {
            e.preventDefault();
            const taskId = this.dataset.taskId;
            const taskElement = this.closest('li');
            
            // Visual feedback
            this.disabled = true;
            this.textContent = 'Deleting...';
            
            try {
                const response = await fetch(`/delete_task/${taskId}`, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin'
                });
                
                if (response.ok) {
                    // Smooth removal animation
                    taskElement.style.transition = 'all 0.3s ease';
                    taskElement.style.opacity = '0';
                    taskElement.style.height = `${taskElement.offsetHeight}px`;
                    
                    // Trigger reflow
                    void taskElement.offsetHeight;
                    
                    // Animate collapse
                    taskElement.style.height = '0';
                    taskElement.style.margin = '0';
                    taskElement.style.padding = '0';
                    
                    // Remove after animation
                    setTimeout(() => {
                        taskElement.remove();
                        
                        // Update reminder counts if needed
                        const reminderSpans = document.querySelectorAll('.reminder-date');
                        if (reminderSpans.length === 0) {
                            const emptyState = document.createElement('p');
                            emptyState.textContent = 'No tasks yet.';
                            document.getElementById('task-list').appendChild(emptyState);
                        }
                    }, 300);
                } else {
                    const error = await response.json();
                    console.error('Delete failed:', error);
                    this.textContent = 'Error!';
                    setTimeout(() => {
                        this.textContent = 'Delete';
                        this.disabled = false;
                    }, 1500);
                }
            } catch (error) {
                console.error('Network error:', error);
                this.textContent = 'Network Error';
                setTimeout(() => {
                    this.textContent = 'Delete';
                    this.disabled = false;
                }, 1500);
            }
        });
    });
});
    </script>
</body>
</html>

 ''', user=user, sortedTasks=sortedTasks, error_msg=error_msg, datetime=datetime, bot_invite_url=bot_invite_url)


@app.route('/delete_task/<int:task_id>', methods=['DELETE'])  # Change to DELETE method
def delete_task(task_id):
    user = session.get('user')
    if not user:
        return {'success': False, 'error': 'Not authenticated'}, 401

    conn = get_db_connection()
    # Verify task belongs to user before deleting
    result = conn.execute(
        'DELETE FROM tasks WHERE id = ? AND user_id = ? RETURNING id',
        (task_id, user['id'])
    ).fetchone()
    conn.commit()
    logger.info(f"User '{user['global_name']}' has manually deleted task '{task_id}' using the website.")

    if result:
        return {'success': True}, 200
    return {'success': False, 'error': 'Task not found'}, 404


@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    user = session.get('user')
    if not user:
        return redirect('/')

    conn = get_db_connection()

    if request.method == 'POST':
        new_task = request.form.get('task')
        new_due_date = request.form.get('due_date')  # User input like "Aug 29 2025"
        new_reminders_str = request.form.get('reminders')

        error_msg = None
        db_date_format = None
        due_date_dt = None
        
        try:
            # ✅ VALIDATE FIRST before converting
            new_reminders = int(new_reminders_str)
            if new_reminders < 1:
                raise ValueError("Reminders must be at least 1.")
            
            # ✅ Validate date format first
            due_date_dt = datetime.strptime(new_due_date, "%b %d %Y")
            
            # ✅ THEN convert to database format after validation
            db_date_format = due_date_dt.strftime("%Y-%m-%d")
                
        except ValueError as e:
            error_msg = str(e)
        except Exception:
            error_msg = "Invalid date format. Please use format like 'Jul 31 2025'"

        # ✅ If there's any error, show the form again WITHOUT updating database
        if error_msg:
            task = conn.execute(
                'SELECT * FROM tasks WHERE id = ? AND user_id = ?',
                (task_id, user['id'])
            ).fetchone()
            return render_template_string('''
                <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
                <div class="edit-task-container">
                <p style="color: red; font-weight: bold;">{{ error_msg }}</p>
                <form method="POST">
                    <label>Task:</label><br>
                    <input name="task" value="{{ new_task if new_task else task['task'] }}" required><br><br>
                    <label>Due date (e.g., Jul 31 2025):</label><br>
                    <input name="due_date" value="{{ new_due_date if new_due_date else task['due_date'] }}" required><br><br>
                    <label>Reminders:</label><br>
                    <input type="number" name="reminders" value="{{ new_reminders_str if new_reminders_str else task['reminders'] }}" min="1"><br><br>
                    <input type="submit" value="Update Task">
                </form>
                <br>
                <a href="/dashboard">← Back to Dashboard</a>
                </div>
            ''', task=task, error_msg=error_msg, new_task=new_task, new_due_date=new_due_date, new_reminders_str=new_reminders_str)

        # ✅ ONLY update database if validation passes
        try:
            # Start transaction
            conn.execute('BEGIN TRANSACTION')
            
            # Update task - use the converted database format
            conn.execute(
                'UPDATE tasks SET task = ?, due_date = ? WHERE id = ? AND user_id = ?',
                (new_task, db_date_format, task_id, user['id'])
            )
            
            # Delete old reminders
            conn.execute('DELETE FROM reminder_dates WHERE task_id = ?', (task_id,))

            # Recalculate and insert new reminders
            today = datetime.today()
            days_left = (due_date_dt - today).days
            interval = days_left / (new_reminders + 1) if new_reminders > 0 else 0
            reminder_dates = []
            
            for i in range(1, new_reminders + 1):
                reminder_day = today + timedelta(days=round(interval * i))
                reminder_dates.append(reminder_day.strftime("%Y-%m-%d"))

            for rd in reminder_dates:
                conn.execute(
                    'INSERT INTO reminder_dates (task_id, reminder_date) VALUES (?, ?)',
                    (task_id, rd)
                )
            
            # Commit only if everything succeeds
            conn.commit()
            logger.info(f"User '{user['global_name']}' has manually edited task '{task_id}' using the website.")
            
        except Exception as e:
            # Rollback if any database operation fails
            conn.rollback()
            error_msg = f"Database error: {str(e)}"
            task = conn.execute(
                'SELECT * FROM tasks WHERE id = ? AND user_id = ?',
                (task_id, user['id'])
            ).fetchone()
            return render_template_string('''
                <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
                <div class="edit-task-container">
                <p style="color: red; font-weight: bold;">{{ error_msg }}</p>
                <form method="POST">
                    <label>Task:</label><br>
                    <input name="task" value="{{ new_task if new_task else task['task'] }}" required><br><br>
                    <label>Due date (e.g., Jul 31 2025):</label><br>
                    <input name="due_date" value="{{ new_due_date if new_due_date else task['due_date'] }}" required><br><br>
                    <label>Reminders:</label><br>
                    <input type="number" name="reminders" value="{{ new_reminders_str if new_reminders_str else task['reminders'] }}" min="1"><br><br>
                    <input type="submit" value="Update Task">
                </form>
                <br>
                <a href="/dashboard">← Back to Dashboard</a>
                </div>
            ''', task=task, error_msg=error_msg, new_task=new_task, new_due_date=new_due_date, new_reminders_str=new_reminders_str)

        return redirect('/dashboard')

    # GET request - show form with current values
    task = conn.execute(
        'SELECT * FROM tasks WHERE id = ? AND user_id = ?',
        (task_id, user['id'])
    ).fetchone()

    if not task:
        return "Task not found", 404

    # Convert database format to display format for the form
    display_date = ""
    try:
        db_date = datetime.strptime(task['due_date'], "%Y-%m-%d")
        display_date = db_date.strftime("%b %d %Y")
    except:
        display_date = task['due_date']  # Fallback if conversion fails

    return render_template_string('''
        <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
        <div class="edit-task-container">
        <h2>Edit Task</h2>
        <form method="POST">
            <label>Task:</label><br>
            <input name="task" value="{{ task['task'] }}" required><br><br>
            <label>Due date (e.g., Jul 31 2025):</label><br>
            <input name="due_date" value="{{ display_date }}" required><br><br>
            <label>Reminders:</label><br>
            <input type="number" name="reminders" value="{{ task['reminders'] if 'reminders' in task.keys() else 1 }}" min="1"><br><br>
            <input type="submit" value="Update Task">
        </form>
        <br>
        <a href="/dashboard">← Back to Dashboard</a>
        </div>
    ''', task=task, display_date=display_date)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')


if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True)