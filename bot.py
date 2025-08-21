import discord
import os
import asyncio
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta
import sqlite3
import logging

logger = logging.getLogger('scheduler_bot') # Unique name for this component
handler = logging.FileHandler('bot.log', mode='a') # Append mode
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


DB_FILE = 'tasks.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # Enable FK support
    return conn


TASKS_FILE = "tasks.json"

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

def add_task_with_reminders(user_id, task_name, due_date_str, reminder_dates):
    """
    reminder_dates: list of strings in format "YYYY-MM-DD" or "Jul 31 2025"
    due_date_str: string date in format "Jul 31 2025" or similar
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO tasks (user_id, task, due_date) VALUES (?, ?, ?)",
        (user_id, task_name, due_date_str)
    )
    task_id = cursor.lastrowid

    for date_str in reminder_dates:
        cursor.execute(
            "INSERT INTO reminder_dates (task_id, reminder_date) VALUES (?, ?)",
            (task_id, date_str)
        )
    conn.commit()
    conn.close()


def create_tables():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task TEXT NOT NULL,
            due_date TEXT NOT NULL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminder_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            reminder_date TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

create_tables()  # Create tables before starting the bot

# Your existing Discord bot code here...

def save_tasks(tasks):
    serializable_tasks = []
    for t in tasks:
        serializable_tasks.append({
            'user_id': t['user_id'],
            'task_name': t['task_name'],
            'due': t['due'].isoformat(),
            'channel_id': t['channel'].id,
            'reminder_dates': [d.isoformat() for d in t['reminder_dates']],
        })
    with open(TASKS_FILE, "w") as f:
        json.dump(serializable_tasks, f, indent=4)

def load_tasks():
    conn = get_db_connection()
    tasks = conn.execute("SELECT * FROM tasks").fetchall()
    conn.close()
    return tasks

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    global tasks
    tasks = load_tasks()  # Load tasks from JSON on startup
    client.loop.create_task(reminder_loop())


async def reminder_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        today_str = datetime.today().strftime("%Y-%m-%d")
        conn = get_db_connection()
        cursor = conn.cursor()

        # Fetch data
        reminders = await fetch_todays_reminders(cursor, today_str)
        to_be_removed = await fetch_past_due_tasks(cursor, today_str)
        reminders_past_due = await fetch_past_due_reminders(cursor, today_str)

        # Process tasks
        await process_past_due_tasks(client, cursor, to_be_removed, today_str)
        await process_todays_reminders(client, cursor, reminders, today_str)
        await cleanup_past_due_reminders(cursor, reminders_past_due, today_str)

        conn.close()
        await asyncio.sleep(3600)


async def fetch_todays_reminders(cursor, today_str):
    cursor.execute("""
        SELECT tasks.id, tasks.user_id, tasks.task, tasks.due_date
        FROM tasks
        JOIN reminder_dates ON tasks.id = reminder_dates.task_id
        WHERE reminder_dates.reminder_date = ?
    """, (today_str,))
    return cursor.fetchall()


async def fetch_past_due_tasks(cursor, today_str):
    cursor.execute("""
        SELECT tasks.id, tasks.user_id, tasks.task, tasks.due_date
        FROM tasks
        WHERE tasks.due_date <= ?
    """, (today_str,))
    return cursor.fetchall()


async def fetch_past_due_reminders(cursor, today_str):
    cursor.execute("""
        SELECT tasks.id, tasks.user_id, tasks.task, tasks.due_date
        FROM tasks
        JOIN reminder_dates ON tasks.id = reminder_dates.task_id
        WHERE reminder_dates.reminder_date < ?
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
                logger.info(f"Successfully deleted and informed past-due task '{task_name}' to user {user}")
        except Exception as e:
            logger.warning(f"Failed to send message to user. Past-due task {task_name} was deleted for {user_id}")
            print(f"Failed to process past-due task for {user_id}: {e}")
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        cursor.connection.commit()
        

async def process_todays_reminders(client, cursor, reminders, today_str):
    for reminder in reminders:
        user_id = int(reminder['user_id'])
        task_name = reminder['task']
        due_date = datetime.strptime(reminder['due_date'], "%Y-%m-%d")

        try:
            user = await client.fetch_user(user_id)
            if user:
                await user.send(
                    f"⏰ Reminder: Your task **{task_name}** is coming up! Due on {due_date.strftime('%B %d, %Y')}."
                )
            logger.info(f"Successfully deleted and informed reminder for '{task_name}' to user {user}")
        except Exception as e:
            logger.warning(f"Failed to send reminder to user. Current reminder {reminder} was deleted for {user_id}")
            print(f"Failed to send reminder to {user_id}: {e}")
        cursor.execute(
                "DELETE FROM reminder_dates WHERE task_id = ? AND reminder_date = ?",
                (reminder['id'], today_str)
            )
        cursor.connection.commit()

async def cleanup_past_due_reminders(cursor, reminders_past_due, today_str):
    for past_due in reminders_past_due:
        try:
            cursor.execute(
                "DELETE FROM reminder_dates WHERE task_id = ? AND reminder_date < ?",
                (past_due['id'], today_str)
            )
            cursor.connection.commit()
            logger.info(f"Successfully deleted past-due reminder for '{task_name}' to user {user}")
        except Exception as e:
            print(f"Failed to remove past-due reminder for task {past_due['id']}: {e}")
            logger.warning(f"Failed to send remove past-due remainder for user {task_name}, {user_id}.")


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.lower() == '!ping':
        await message.channel.send('Pong!')

    if message.content.startswith("!remove"):
        content = message.content[8:]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tasks WHERE task = ? AND user_id = ?",
            (content, str(message.author.id))
        )
        conn.commit()
        if cursor.rowcount > 0:
            await message.channel.send(f"✅ {content} has been removed.")
        else:
            await message.channel.send(f"❌ No task named **{content}** found.")
        conn.close()

        
    
    if message.content == "!upcoming":
        conn = get_db_connection()
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE user_id = ?", (str(message.author.id),)
        ).fetchall()
        conn.close()

        if not tasks:
            await message.channel.send("You have no upcoming tasks!")
            return

        output = "📋 Your Upcoming Tasks:\n\n"
        today_date = datetime.today().date()

        sortedTasks = sorted(tasks, key = lambda x : x['due_date'])
        for task in sortedTasks:
            # due_date stored in YYYY-MM-DD, so parse accordingly
            due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").date()
            days_left = (due_date - today_date).days
            output += f"🌐 **{task['task']}** due on **{due_date.strftime('%A, %b %d, %Y')}** — in {days_left} day(s)\n"

        await message.channel.send(output)


    if message.content.startswith('!schedule'):
        try:
            # Format: !schedule TaskName | Jul 31 2025 | 3
            content = message.content[9:].strip()
            parts = content.split(' | ')
            if len(parts) != 3:
                await message.channel.send("Usage: !schedule TaskName | Jul 31 2025 | numberOfReminders")
                return

            task_name = parts[0].strip()
            due_date = datetime.strptime(parts[1].strip(), "%b %d %Y")
            num_reminders = int(parts[2].strip())

            if num_reminders > 10 or num_reminders < 1:
                raise ValueError("Number of reminders cannot exceed 10 or be less than 1")

            today = datetime.today()
            days_left = (due_date - today).days
            interval = days_left / (num_reminders + 1)

            # Compute reminder dates as list of strings in ISO format
            reminder_dates = []
            for i in range(1, num_reminders + 1):
                reminder_day = today + timedelta(days=round(interval * i))
                reminder_dates.append(reminder_day.strftime("%Y-%m-%d"))

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


client.run(TOKEN)
