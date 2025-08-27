import discord
import os
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
import botserver

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------------- Database ---------------- #
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            task TEXT NOT NULL,
            due_date TEXT NOT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminder_dates (
            id SERIAL PRIMARY KEY,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            reminder_date TEXT NOT NULL
        );
    """)

    # Insert sample data if empty
    cursor.execute("SELECT COUNT(*) FROM tasks")
    if cursor.fetchone()['count'] == 0:
        sample_tasks = [
            ('1347297619063607297', 'Learn Python Deployment', '2025-08-31'),
            ('1347297619063607297', 'Build Discord Bot', '2025-09-10'),
            ('1347297619063607297', 'Deploy to Render', '2025-09-20')
        ]
        cursor.executemany(
            "INSERT INTO tasks (user_id, task, due_date) VALUES (%s, %s, %s)",
            sample_tasks
        )

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Database initialized successfully!")

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
    conn = get_db_connection()
    cursor = conn.cursor()

    # Insert task and get task_id
    cursor.execute(
        "INSERT INTO tasks (user_id, task, due_date) VALUES (%s, %s, %s) RETURNING id",
        (user_id, task_name, due_date_str)
    )
    task_id = cursor.fetchone()['id']

    # Insert reminders
    for date_str in reminder_dates:
        cursor.execute(
            "INSERT INTO reminder_dates (task_id, reminder_date) VALUES (%s, %s)",
            (task_id, date_str)
        )

    conn.commit()
    cursor.close()
    conn.close()
    logger.info(f"User {user_id} added task '{task_name}' with reminders {reminder_dates}.")

def load_tasks():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks")
    tasks = cursor.fetchall()
    cursor.close()
    conn.close()
    return tasks

# ---------------- Reminder Loop ---------------- #
async def fetch_todays_reminders(cursor, today_str):
    cursor.execute("""
        SELECT tasks.id, tasks.user_id, tasks.task, tasks.due_date
        FROM tasks
        JOIN reminder_dates ON tasks.id = reminder_dates.task_id
        WHERE reminder_dates.reminder_date = %s
    """, (today_str,))
    return cursor.fetchall()

async def fetch_past_due_tasks(cursor, today_str):
    cursor.execute("""
        SELECT * FROM tasks
        WHERE due_date <= %s
    """, (today_str,))
    return cursor.fetchall()

async def fetch_past_due_reminders(cursor, today_str):
    cursor.execute("""
        SELECT tasks.id, tasks.user_id, tasks.task, tasks.due_date
        FROM tasks
        JOIN reminder_dates ON tasks.id = reminder_dates.task_id
        WHERE reminder_dates.reminder_date < %s
    """, (today_str,))
    return cursor.fetchall()

async def process_past_due_tasks(client, cursor, tasks, today_str):
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
        cursor.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        cursor.connection.commit()

async def process_todays_reminders(client, cursor, reminders, today_str):
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
        cursor.execute(
            "DELETE FROM reminder_dates WHERE task_id = %s AND reminder_date = %s",
            (task_id, today_str)
        )
        cursor.connection.commit()

async def cleanup_past_due_reminders(cursor, reminders_past_due, today_str):
    for past_due in reminders_past_due:
        try:
            cursor.execute(
                "DELETE FROM reminder_dates WHERE task_id = %s AND reminder_date < %s",
                (past_due['id'], today_str)
            )
            cursor.connection.commit()
            logger.info(f"Deleted past-due reminder {past_due['id']}")
        except Exception as e:
            logger.warning(f"Failed to delete past-due reminder {past_due['id']}: {e}")

async def reminder_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        today_str = datetime.today().strftime("%Y-%m-%d")
        conn = get_db_connection()
        cursor = conn.cursor()

        reminders = await fetch_todays_reminders(cursor, today_str)
        past_due_tasks = await fetch_past_due_tasks(cursor, today_str)
        past_due_reminders = await fetch_past_due_reminders(cursor, today_str)

        await process_past_due_tasks(client, cursor, past_due_tasks, today_str)
        await process_todays_reminders(client, cursor, reminders, today_str)
        await cleanup_past_due_reminders(cursor, past_due_reminders, today_str)

        cursor.close()
        conn.close()
        await asyncio.sleep(3600)

# ---------------- Discord Events ---------------- #
@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    client.loop.create_task(reminder_loop())

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    if message.content.lower() == '!ping':
        await message.channel.send('Pong!')

    elif message.content.startswith("!remove"):
        task_name = message.content[8:].strip()
        cursor.execute(
            "DELETE FROM tasks WHERE task = %s AND user_id = %s",
            (task_name, str(message.author.id))
        )
        conn.commit()
        if cursor.rowcount > 0:
            await message.channel.send(f"✅ {task_name} has been removed.")
        else:
            await message.channel.send(f"❌ No task named **{task_name}** found.")
        logger.info(f"User {message.author.id} removed task '{task_name}'")

    elif message.content == "!upcoming":
        cursor.execute(
            "SELECT * FROM tasks WHERE user_id = %s ORDER BY due_date",
            (str(message.author.id),)
        )
        tasks = cursor.fetchall()
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

    cursor.close()
    conn.close()

# ---------------- Run Bot ---------------- #
client.run(TOKEN)
botserver.keep_alive()
