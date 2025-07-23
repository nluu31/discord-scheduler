import discord
import os
import asyncio
import json
from dotenv import load_dotenv
from datetime import datetime, timedelta

TASKS_FILE = "tasks.json"

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

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
    try:
        with open(TASKS_FILE, "r") as f:
            raw_tasks = json.load(f)
        loaded_tasks = []
        for t in raw_tasks:
            loaded_tasks.append({
                'user_id': t['user_id'],
                'task_name': t['task_name'],
                'due': datetime.fromisoformat(t['due']),
                'channel': client.get_channel(t['channel_id']),
                'reminder_dates': [datetime.fromisoformat(d).date() for d in t['reminder_dates']],
            })
        return loaded_tasks
    except (FileNotFoundError, json.JSONDecodeError):
        return []

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    global tasks
    tasks = load_tasks()  # Load tasks from JSON on startup
    client.loop.create_task(reminder_loop())

async def reminder_loop():
    await client.wait_until_ready()
    global tasks

    while not client.is_closed():
        now = datetime.today()
        changed = False

        for task in tasks[:]:
            if now >= task['due']:
                try:
                    await task['channel'].send(f"<@{task['user_id']}> ⏰ Reminder: Your task '{task['task_name']}' is due!")
                except discord.Forbidden:
                    print(f"Could not send message in this channel.")
                tasks.remove(task)
                changed = True
            elif now.date() in task['reminder_dates']:
                try:
                    await task['channel'].send(f"<@{task['user_id']}> ⏰ Reminder: Your task '{task['task_name']}' is due on {task['due'].strftime('%A, %b %d, %Y')}!")
                    task['reminder_dates'].remove(now.date())
                    changed = True
                except discord.Forbidden:
                    print("Could not send message in this channel.")
            

        if changed:
            save_tasks(tasks)

        await asyncio.sleep(10)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.lower() == '!ping':
        await message.channel.send('Pong!')

    if message.content.startswith("!remove"):
    try:
        content = message.content[7:].strip()
        found = False
        for i, task in enumerate(tasks):
            if task['task_name'] == content:
                tasks.pop(i)
                save_tasks(tasks)  # Save after removal
                await message.channel.send(f"✅ {content} has been successfully removed from your schedule")
                found = True
                break
        if not found:
            await message.channel.send(f"There is no task named {content} coming up")
    except Exception:
        await message.channel.send("Error has occured")

        
    
    if message.content == "!upcoming":
        try: 
            if len(tasks) == 0:
                await message.channel.send("You have no upcoming tasks!")
            else:
                sorted_by_date = sorted(tasks, key = lambda x: x['due'])
                output = "Your Upcoming Tasks are: \n"
                for task in sorted_by_date:
                    delta = (task['due'].date() - datetime.today().date()).days
                    output += f"🌌 **{task['task_name']}** due on **{task['due'].strftime('%A, %b %d, %Y')}** due in {delta} day(s)\n"
                await message.channel.send(output)
                
        except Exception:
            await message.channel.send("Error has occured")


    if message.content.startswith('!schedule'):
        try:
            content = message.content[9:].strip()
            parts = content.split(' | ')
            if len(parts) != 3:
                await message.channel.send("Usage: !schedule TaskName | time (e.g., jul 31 2025) | numberOfReminders")
                return

            task_name = parts[0].strip()
            due_date = datetime.strptime(parts[1].strip(), "%b %d %Y")
            numReminders = int(parts[2].strip())

            today = datetime.today().date()
            daysLeft = (due_date.date() -  today).days
            interval = daysLeft / (numReminders + 1)

            reminder_dates = [today + timedelta(days=round(interval * i)) for i in range(1, numReminders + 1)]
            for i, d in enumerate(reminder_dates, 1):
                print(f"Reminder {i}: {d}")

            tasks.append({
                'user_id': message.author.id,
                'task_name': task_name,
                'due': due_date,
                'channel' : message.channel,
                'reminder_dates' : reminder_dates
            })

            save_tasks(tasks)  # <-- SAVE TASKS HERE!

            reminder_list = "\n".join(
                [f"Reminder {i}: {d.strftime('%A, %b %d, %Y')}" for i, d in enumerate(reminder_dates, 1)]
            )

            await message.channel.send(
                f"✅ Task **'{task_name}'** has been scheduled for **{due_date.strftime('%A, %b %d, %Y')}**.\n"
                f"You will receive {numReminders} reminder(s) on these days:\n\n{reminder_list}"
            )

        except Exception:
            await message.channel.send("Error: Use format: `!schedule TaskName | jul 31 2025 | 3`")

    # Your other commands (!upcoming, !remove) here (no changes)

client.run(TOKEN)
