import discord
import os
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta

tasks = []
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True  # Enable reading message content

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')
    client.loop.create_task(reminder_loop())

async def reminder_loop():
    await client.wait_until_ready()

    while not client.is_closed():
        now = datetime.utcnow()
        for task in tasks[:]:
            if now >= task['due']:
                try:
                    await task['channel'].send(f"<@{task['user_id']}> ⏰ Reminder: Your task '{task['task_name']}' is due!")
                except discord.Forbidden:
                    print(f"Could not send message in this channel.")
                tasks.remove(task)
        await asyncio.sleep(10)


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.lower() == '!ping':
        await message.channel.send('Pong!')

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
                'channel' : message.channel
            })
            print(tasks)

            reminder_list = "\n".join(
                [f"Reminder {i}: {d.strftime('%A, %b %d, %Y')}" for i, d in enumerate(reminder_dates, 1)]
            )

            await message.channel.send(
                f"✅ Task **'{task_name}'** has been scheduled for **{due_date.strftime('%A, %b %d, %Y')}**.\n"
                f"You will receive {numReminders} reminder(s) on these days:\n\n{reminder_list}"
            )

        except Exception:
            await message.channel.send("Error: Use format: `!schedule TaskName | jul 31 2025 | 3`")


client.run(TOKEN)

