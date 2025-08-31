import discord
import os
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta
import logging
from supabase import create_client
import botserver

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------- Database ---------------- #
def init_database():
    """Initialize database with sample data if needed"""
    try:
        # Check if we have any tasks
        result = supabase.table('tasks').select('id').limit(1).execute()
        
        # If no tasks exist, insert sample data
        if not result.data:
            sample_tasks = [
                {
                    'user_id': '1347297619063607297',
                    'task': 'Learn Python Deployment',
                    'due_date': '2025-08-31'
                },
                {
                    'user_id': '1347297619063607297',
                    'task': 'Build Discord Bot',
                    'due_date': '2025-09-10'
                },
                {
                    'user_id': '1347297619063607297',
                    'task': 'Deploy to Render',
                    'due_date': '2025-09-20'
                }
            ]
            supabase.table('tasks').insert(sample_tasks).execute()
            print("✅ Sample data inserted!")
        
        print("✅ Database initialized successfully!")
    except Exception as e:
        print(f"❌ Database initialization error: {e}")

init_database()

# ---------------- Logging ---------------- #
logger = logging.getLogger('scheduler_bot')
handler = logging.FileHandler('bot.log', mode='a')
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------------- Discord Bot ---------------- #
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# ---------------- Helper Functions ---------------- #
def add_task_with_reminders(user_id, task_name, due_date_str, reminder_dates):
    """Add a task with its reminder dates using Supabase"""
    try:
        # Insert task and get task_id
        task_result = supabase.table('tasks').insert({
            'user_id': user_id,
            'task': task_name,
            'due_date': due_date_str
        }).execute()

        if not task_result.data:
            raise Exception("Failed to insert task")
        
        task_id = task_result.data[0]['id']

        # Prepare reminder dates for batch insert
        reminder_data = []
        for date_str in reminder_dates:
            reminder_data.append({
                'task_id': task_id,
                'reminder_date': date_str
            })

        # Insert reminders
        if reminder_data:
            supabase.table('reminder_dates').insert(reminder_data).execute()

        logger.info(f"User {user_id} added task '{task_name}' with reminders {reminder_dates}.")
    except Exception as e:
        logger.error(f"Error adding task with reminders: {e}")
        raise

def load_tasks():
    """Load all tasks from database"""
    try:
        result = supabase.table('tasks').select('*').execute()
        return result.data
    except Exception as e:
        logger.error(f"Error loading tasks: {e}")
        return []

# ---------------- Reminder Loop Functions ---------------- #
async def fetch_todays_reminders(today_str):
    """Fetch reminders due today"""
    try:
        result = supabase.table('tasks').select('''
            id, user_id, task, due_date,
            reminder_dates!inner(reminder_date)
        ''').eq('reminder_dates.reminder_date', today_str).execute()
        
        return result.data
    except Exception as e:
        logger.error(f"Error fetching today's reminders: {e}")
        return []

async def fetch_past_due_tasks(today_str):
    """Fetch tasks that are past due"""
    try:
        result = supabase.table('tasks').select('*').lte('due_date', today_str).execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching past due tasks: {e}")
        return []

async def fetch_past_due_reminders(today_str):
    """Fetch reminders that are past due"""
    try:
        result = supabase.table('tasks').select('''
            id, user_id, task, due_date,
            reminder_dates!inner(reminder_date)
        ''').lt('reminder_dates.reminder_date', today_str).execute()
        
        return result.data
    except Exception as e:
        logger.error(f"Error fetching past due reminders: {e}")
        return []

async def process_past_due_tasks(client, tasks, today_str):
    """Process and notify users of past due tasks"""
    for task in tasks:
        user_id = int(task['user_id'])
        task_name = task['task']
        task_id = task['id']
        due_date = datetime.strptime(task['due_date'], "%Y-%m-%d")
        
        try:
            user = await client.fetch_user(user_id)
            if user:
                await user.send(
                    f"⚠️ Alert: Your task **{task_name}** is due today (or was due on {due_date.strftime('%B %d, %Y')})!"
                )
                logger.info(f"Notified past-due task '{task_name}' to user {user}")
        except Exception as e:
            logger.warning(f"Failed to send past-due task '{task_name}' to user {user_id}: {e}")
        
        # Delete the task
        try:
            supabase.table('tasks').delete().eq('id', task_id).execute()
        except Exception as e:
            logger.error(f"Failed to delete past-due task {task_id}: {e}")

async def process_todays_reminders(client, reminders, today_str):
    """Process and send today's reminders"""
    for reminder in reminders:
        user_id = int(reminder['user_id'])
        task_name = reminder['task']
        task_id = reminder['id']
        due_date = datetime.strptime(reminder['due_date'], "%Y-%m-%d")
        
        try:
            user = await client.fetch_user(user_id)
            if user:
                await user.send(
                    f"⏰ Reminder: Your task **{task_name}** is coming up! Due on {due_date.strftime('%B %d, %Y')}."
                )
            logger.info(f"Notified reminder for '{task_name}' to user {user}")
        except Exception as e:
            logger.warning(f"Failed to send reminder for '{task_name}' to user {user_id}: {e}")
        
        # Delete the specific reminder
        try:
            supabase.table('reminder_dates').delete().eq('task_id', task_id).eq('reminder_date', today_str).execute()
        except Exception as e:
            logger.error(f"Failed to delete reminder for task {task_id}: {e}")

async def cleanup_past_due_reminders(reminders_past_due, today_str):
    """Clean up past due reminders"""
    for past_due in reminders_past_due:
        try:
            supabase.table('reminder_dates').delete().eq('task_id', past_due['id']).lt('reminder_date', today_str).execute()
            logger.info(f"Deleted past-due reminder {past_due['id']}")
        except Exception as e:
            logger.warning(f"Failed to delete past-due reminder {past_due['id']}: {e}")

async def reminder_loop():
    """Main reminder loop that runs every hour"""
    await client.wait_until_ready()
    while not client.is_closed():
        today_str = datetime.today().strftime("%Y-%m-%d")
        
        try:
            reminders = await fetch_todays_reminders(today_str)
            past_due_tasks = await fetch_past_due_tasks(today_str)
            past_due_reminders = await fetch_past_due_reminders(today_str)

            await process_past_due_tasks(client, past_due_tasks, today_str)
            await process_todays_reminders(client, reminders, today_str)
            await cleanup_past_due_reminders(past_due_reminders, today_str)
        except Exception as e:
            logger.error(f"Error in reminder loop: {e}")

        await asyncio.sleep(3600)  # Wait 1 hour

# ---------------- Discord Events ---------------- #
@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    client.loop.create_task(reminder_loop())

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    try:
        if message.content.lower() == '!ping':
            await message.channel.send('Pong!')

        elif message.content.startswith("!remove"):
            task_name = message.content[8:].strip()
            
            # Delete task using Supabase
            result = supabase.table('tasks').delete().eq('task', task_name).eq('user_id', str(message.author.id)).execute()
            
            if result.data:
                await message.channel.send(f"✅ {task_name} has been removed.")
                logger.info(f"User {message.author.id} removed task '{task_name}'")
            else:
                await message.channel.send(f"❌ No task named **{task_name}** found.")

        elif message.content == "!upcoming":
            # Fetch user's tasks
            result = supabase.table('tasks').select('*').eq('user_id', str(message.author.id)).order('due_date').execute()
            tasks = result.data
            
            if not tasks:
                await message.channel.send("You have no upcoming tasks!")
            else:
                output = "📋 Your Upcoming Tasks:\n\n"
                today_date = datetime.today().date()
                for task in tasks:
                    due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").date()
                    days_left = (due_date - today_date).days
                    output += f"🌐 **{task['task']}** due on **{due_date.strftime('%A, %b %d, %Y')}** — in {days_left} day(s)\n"
                await message.channel.send(output)

        elif message.content.startswith('!schedule'):
            try:
                # Format: !schedule TaskName | Jul 31 2025 | 3
                parts = message.content[9:].strip().split(' | ')
                if len(parts) != 3:
                    await message.channel.send("Usage: !schedule TaskName | Jul 31 2025 | numberOfReminders")
                    return

                task_name = parts[0].strip()
                due_date = datetime.strptime(parts[1].strip(), "%b %d %Y")
                num_reminders = int(parts[2].strip())
                
                if not (1 <= num_reminders <= 10):
                    raise ValueError("Number of reminders must be between 1 and 10")

                today = datetime.today()
                days_left = (due_date - today).days
                interval = days_left / (num_reminders + 1)

                reminder_dates = [
                    (today + timedelta(days=round(interval * i))).strftime("%Y-%m-%d")
                    for i in range(1, num_reminders + 1)
                ]

                add_task_with_reminders(
                    str(message.author.id),
                    task_name,
                    due_date.strftime("%Y-%m-%d"),
                    reminder_dates
                )

                reminders_text = "\n".join([f"Reminder {i+1}: {d}" for i, d in enumerate(reminder_dates)])
                await message.channel.send(
                    f"✅ Task **{task_name}** scheduled for **{due_date.strftime('%A, %b %d, %Y')}** with reminders on:\n{reminders_text}"
                )
            except Exception as e:
                await message.channel.send(f"Error scheduling task: {e}")
                logger.error(f"Error in !schedule command: {e}")

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await message.channel.send("❌ An error occurred while processing your request.")

# ---------------- Run Bot ---------------- #
client.run(TOKEN)
# botserver.keep_alive()