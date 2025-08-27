import os
from flask import Flask, redirect, url_for, session, request, render_template_string, g
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import logging

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret_key')

logger = logging.getLogger('scheduler_webapp')
handler = logging.FileHandler('webapp.log', mode='a')
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

# Supabase/PostgreSQL connection configuration
def get_db_connection():
    if 'db' not in g:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        conn.autocommit = False  # We'll handle transactions manually
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create tasks table with PostgreSQL syntax
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            task TEXT NOT NULL,
            due_date DATE NOT NULL
        );
    ''')
    
    # Create reminder_dates table with proper foreign key
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminder_dates (
            id SERIAL PRIMARY KEY,
            task_id INTEGER NOT NULL,
            reminder_date DATE NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
    ''')
    
    conn.commit()

def add_task_with_reminders(user_id, task_name, due_date_str, num_reminders):
    conn = get_db_connection()
    cursor = conn.cursor()
    user = session.get('user')

    try:
        # Convert to ISO format before inserting
        due_date = datetime.strptime(due_date_str.strip(), "%b %d %Y")
        due_date_iso = due_date.strftime("%Y-%m-%d")

        # Insert task and get the ID
        cursor.execute(
            'INSERT INTO tasks (user_id, task, due_date) VALUES (%s, %s, %s) RETURNING id',
            (user_id, task_name, due_date_iso)
        )
        task_id = cursor.fetchone()[0]

        # Calculate reminder dates
        today = datetime.today()
        days_left = (due_date - today).days
        interval = days_left / (num_reminders + 1) if num_reminders > 0 else 0

        # Insert reminder dates
        for i in range(1, num_reminders + 1):
            reminder_day = today + timedelta(days=round(interval * i))
            reminder_date_str = reminder_day.strftime("%Y-%m-%d")
            cursor.execute(
                'INSERT INTO reminder_dates (task_id, reminder_date) VALUES (%s, %s)',
                (task_id, reminder_date_str)
            )
        
        conn.commit()
        logger.info(f"User '{user['global_name']}' has manually added task '{task_name}' using the website.")
    
    except Exception as e:
        conn.rollback()
        logger.error(f"Error adding task: {e}")
        raise

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
                raise ValueError("Number of reminders cannot exceed 10.")
            datetime.strptime(due_date, "%b %d %Y")
        except Exception:
            error_msg = "Invalid input. Please check your due date format (e.g., Jul 31 2025) and reminders (positive integer)."
        else:
            try:
                add_task_with_reminders(user_id, task_name, due_date, num_reminders)
            except Exception as e:
                error_msg = "Failed to add task. Please try again."
                logger.error(f"Task addition failed: {e}")

    # Fetch tasks with reminder count
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cursor.execute('''
        SELECT t.id, t.task, t.due_date, COUNT(r.id) AS reminders 
        FROM tasks t 
        LEFT JOIN reminder_dates r ON t.id = r.task_id 
        WHERE t.user_id = %s 
        GROUP BY t.id, t.task, t.due_date 
        ORDER BY t.due_date
    ''', (user_id,))
    
    tasks = cursor.fetchall()
    
    # Generate bot invite URL
    bot_client_id = os.getenv('DISCORD_BOT_CLIENT_ID', os.getenv('DISCORD_CLIENT_ID'))
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
     
    <h1>Please Enter your Tasks:</h1>

    {% if error_msg %}
        <p class="error">{{ error_msg }}</p>
    {% endif %}

    <form method="POST" class="task-form">
        <input type="text" name="task" placeholder="Task name" required>
        <input type="text" name="due_date" placeholder="Due date (e.g., Aug 31 2025)" required>
        <input type="number" name="reminders" placeholder="#" min="1" value="1">
        <input type="submit" value="Add">
    </form>

    <div class="dropdown-container">
        <button class="toggle-btn" onclick="toggleTasks()" id="toggle-btn">Show Tasks ▼</button>

        <ul class="task-list" id="task-list">
            {% for task in tasks %}
                <li>
                    <div class="task-content">
                        <strong>{{ task['task'] }}</strong>
                        <div class="task-meta">
                            <span>Due: {{ task['due_date'].strftime('%A, %B %d, %Y') if task['due_date'].__class__.__name__ == 'date' else datetime.strptime(task['due_date'], '%Y-%m-%d').strftime('%A, %B %d, %Y') }}</span>
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
    </div>

    <a href="/logout" class="logout">logout {{ user['global_name'] }}</a>

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
        document.body.style.overflow = 'hidden';
    }

    function hideBotInvitePopup(event) {
        if (!event || event.target === event.currentTarget || event.target.classList.contains('close-btn')) {
            const popup = document.getElementById('botInvitePopup');
            popup.classList.remove('show');
            document.body.style.overflow = '';
        }
    }

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            hideBotInvitePopup();
        }
    });

    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', async function(e) {
                e.preventDefault();
                const taskId = this.dataset.taskId;
                const taskElement = this.closest('li');
                
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
                        taskElement.style.transition = 'all 0.3s ease';
                        taskElement.style.opacity = '0';
                        taskElement.style.height = `${taskElement.offsetHeight}px`;
                        
                        void taskElement.offsetHeight;
                        
                        taskElement.style.height = '0';
                        taskElement.style.margin = '0';
                        taskElement.style.padding = '0';
                        
                        setTimeout(() => {
                            taskElement.remove();
                            
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
    ''', user=user, tasks=tasks, error_msg=error_msg, datetime=datetime, bot_invite_url=bot_invite_url)

@app.route('/delete_task/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    user = session.get('user')
    if not user:
        return {'success': False, 'error': 'Not authenticated'}, 401

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Verify task belongs to user before deleting
        cursor.execute(
            'DELETE FROM tasks WHERE id = %s AND user_id = %s RETURNING id',
            (task_id, user['id'])
        )
        result = cursor.fetchone()
        conn.commit()
        
        if result:
            logger.info(f"User '{user['global_name']}' has manually deleted task '{task_id}' using the website.")
            return {'success': True}, 200
        else:
            return {'success': False, 'error': 'Task not found'}, 404
            
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting task {task_id}: {e}")
        return {'success': False, 'error': 'Database error'}, 500

@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    user = session.get('user')
    if not user:
        return redirect('/')

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if request.method == 'POST':
        new_task = request.form.get('task')
        new_due_date = request.form.get('due_date')
        new_reminders_str = request.form.get('reminders')

        error_msg = None
        db_date_format = None
        due_date_dt = None
        
        try:
            new_reminders = int(new_reminders_str)
            if new_reminders < 1:
                raise ValueError("Reminders must be at least 1.")
            
            due_date_dt = datetime.strptime(new_due_date, "%b %d %Y")
            db_date_format = due_date_dt.strftime("%Y-%m-%d")
                
        except ValueError as e:
            error_msg = str(e)
        except Exception:
            error_msg = "Invalid date format. Please use format like 'Jul 31 2025'"

        if error_msg:
            cursor.execute(
                'SELECT * FROM tasks WHERE id = %s AND user_id = %s',
                (task_id, user['id'])
            )
            task = cursor.fetchone()
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
                    <input type="number" name="reminders" value="{{ new_reminders_str if new_reminders_str else 1 }}" min="1"><br><br>
                    <input type="submit" value="Update Task">
                </form>
                <br>
                <a href="/dashboard">← Back to Dashboard</a>
                </div>
            ''', task=task, error_msg=error_msg, new_task=new_task, new_due_date=new_due_date, new_reminders_str=new_reminders_str)

        # Update database
        try:
            cursor.execute('BEGIN')
            
            cursor.execute(
                'UPDATE tasks SET task = %s, due_date = %s WHERE id = %s AND user_id = %s',
                (new_task, db_date_format, task_id, user['id'])
            )
            
            cursor.execute('DELETE FROM reminder_dates WHERE task_id = %s', (task_id,))

            # Recalculate and insert new reminders
            today = datetime.today()
            days_left = (due_date_dt - today).days
            interval = days_left / (new_reminders + 1) if new_reminders > 0 else 0
            
            for i in range(1, new_reminders + 1):
                reminder_day = today + timedelta(days=round(interval * i))
                reminder_date_str = reminder_day.strftime("%Y-%m-%d")
                cursor.execute(
                    'INSERT INTO reminder_dates (task_id, reminder_date) VALUES (%s, %s)',
                    (task_id, reminder_date_str)
                )
            
            conn.commit()
            logger.info(f"User '{user['global_name']}' has manually edited task '{task_id}' using the website.")
            
        except Exception as e:
            conn.rollback()
            error_msg = f"Database error: {str(e)}"
            cursor.execute(
                'SELECT * FROM tasks WHERE id = %s AND user_id = %s',
                (task_id, user['id'])
            )
            task = cursor.fetchone()
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
                    <input type="number" name="reminders" value="{{ new_reminders_str if new_reminders_str else 1 }}" min="1"><br><br>
                    <input type="submit" value="Update Task">
                </form>
                <br>
                <a href="/dashboard">← Back to Dashboard</a>
                </div>
            ''', task=task, error_msg=error_msg, new_task=new_task, new_due_date=new_due_date, new_reminders_str=new_reminders_str)

        return redirect('/dashboard')

    # GET request - show form with current values
    cursor.execute(
        'SELECT * FROM tasks WHERE id = %s AND user_id = %s',
        (task_id, user['id'])
    )
    task = cursor.fetchone()

    if not task:
        return "Task not found", 404

    # Convert database format to display format
    display_date = ""
    try:
        if hasattr(task['due_date'], 'strftime'):
            # It's already a date object
            display_date = task['due_date'].strftime("%b %d %Y")
        else:
            # It's a string, parse it first
            db_date = datetime.strptime(str(task['due_date']), "%Y-%m-%d")
            display_date = db_date.strftime("%b %d %Y")
    except:
        display_date = str(task['due_date'])

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
            <input type="number" name="reminders" value="1" min="1"><br><br>
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