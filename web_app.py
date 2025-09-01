import os
from flask import Flask, redirect, url_for, session, request, render_template_string
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import datetime, timedelta
import logging

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()  # Load .env variables first

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

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

def init_db():
    """Initialize database tables using Supabase"""
    try:
        # Create tasks table - Supabase will handle SERIAL/AUTO_INCREMENT with id
        supabase.rpc('create_tasks_table_if_not_exists').execute()
        
        # Create reminder_dates table
        supabase.rpc('create_reminder_dates_table_if_not_exists').execute()
        
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

def add_task_with_reminders(user_id, task_name, due_date_str, num_reminders):
    user = session.get('user')

    try:
        # Convert to ISO format before inserting
        due_date = datetime.strptime(due_date_str.strip(), "%b %d %Y")
        due_date_iso = due_date.strftime("%Y-%m-%d")

        # Insert task using Supabase
        task_result = supabase.table('tasks').insert({
            'user_id': user_id,
            'task': task_name,
            'due_date': due_date_iso
        }).execute()

        if not task_result.data:
            raise Exception("Failed to insert task")
        
        task_id = task_result.data[0]['id']

        # Calculate reminder dates
        today = datetime.today()
        days_left = (due_date - today).days
        interval = days_left / (num_reminders + 1) if num_reminders > 0 else 0

        # Prepare reminder dates for batch insert
        reminder_dates = []
        for i in range(1, num_reminders + 1):
            reminder_day = today + timedelta(days=round(interval * i))
            reminder_date_str = reminder_day.strftime("%Y-%m-%d")
            reminder_dates.append({
                'task_id': task_id,
                'reminder_date': reminder_date_str
            })

        # Batch insert reminder dates
        if reminder_dates:
            supabase.table('reminder_dates').insert(reminder_dates).execute()
        
        logger.info(f"User '{user['global_name']}' has manually added task '{task_name}' using the website.")
    
    except Exception as e:
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

    # Fetch tasks with reminder count using Supabase
    try:
        # Get tasks for user
        tasks_result = supabase.table('tasks').select('*').eq('user_id', user_id).order('due_date').execute()
        
        tasks_with_reminders = []
        for task in tasks_result.data:
            # Get reminder count for each task
            reminders_result = supabase.table('reminder_dates').select('id').eq('task_id', task['id']).execute()
            reminder_count = len(reminders_result.data)
            
            task['reminders'] = reminder_count
            tasks_with_reminders.append(task)
        
        tasks = tasks_with_reminders
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        tasks = []
    
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
                            <span>Due: {{ format_date(task['due_date']) }}</span>
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
    ''', user=user, tasks=tasks, error_msg=error_msg, format_date=format_date, bot_invite_url=bot_invite_url)

@app.route('/delete_task/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    user = session.get('user')
    if not user:
        return {'success': False, 'error': 'Not authenticated'}, 401
    
    try:
        # Delete task using Supabase (reminder_dates will be deleted via CASCADE)
        result = supabase.table('tasks').delete().eq('id', task_id).eq('user_id', user['id']).execute()
        
        if result.data:
            logger.info(f"User '{user['global_name']}' has manually deleted task '{task_id}' using the website.")
            return {'success': True}, 200
        else:
            return {'success': False, 'error': 'Task not found'}, 404
            
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        return {'success': False, 'error': 'Database error'}, 500

@app.route('/edit_task/<int:task_id>', methods=['GET', 'POST'])
def edit_task(task_id):
    user = session.get('user')
    if not user:
        return redirect('/')

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
            # Get task for error display
            task_result = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user['id']).execute()
            task = task_result.data[0] if task_result.data else None
            
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

        # Update database using Supabase
        try:
            # Update task
            supabase.table('tasks').update({
                'task': new_task,
                'due_date': db_date_format
            }).eq('id', task_id).eq('user_id', user['id']).execute()
            
            # Delete existing reminders
            supabase.table('reminder_dates').delete().eq('task_id', task_id).execute()

            # Recalculate and insert new reminders
            today = datetime.today()
            days_left = (due_date_dt - today).days
            interval = days_left / (new_reminders + 1) if new_reminders > 0 else 0
            
            reminder_dates = []
            for i in range(1, new_reminders + 1):
                reminder_day = today + timedelta(days=round(interval * i))
                reminder_date_str = reminder_day.strftime("%Y-%m-%d")
                reminder_dates.append({
                    'task_id': task_id,
                    'reminder_date': reminder_date_str
                })
            
            if reminder_dates:
                supabase.table('reminder_dates').insert(reminder_dates).execute()
            
            logger.info(f"User '{user['global_name']}' has manually edited task '{task_id}' using the website.")
            
        except Exception as e:
            error_msg = f"Database error: {str(e)}"
            task_result = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user['id']).execute()
            task = task_result.data[0] if task_result.data else None
            
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
    try:
        task_result = supabase.table('tasks').select('*').eq('id', task_id).eq('user_id', user['id']).execute()
        
        if not task_result.data:
            return "Task not found", 404
        
        task = task_result.data[0]
    except Exception as e:
        logger.error(f"Error fetching task for edit: {e}")
        return "Error loading task", 500

    # Convert database format to display format
    display_date = ""
    try:
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

def format_date(date_str):
    """Helper function to format dates for display"""
    try:
        date_obj = datetime.strptime(str(date_str), "%Y-%m-%d")
        return date_obj.strftime('%A, %B %d, %Y')
    except:
        return str(date_str)

if __name__ == '__main__':
    with app.app_context():
        init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)