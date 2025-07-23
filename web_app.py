from flask import Flask, request, jsonify
import json
from datetime import datetime, timedelta
import os

app = Flask(__name__)
TASKS_FILE = "tasks.json"
DISCORD_CHANNEL_ID = 123456789012345678  # Replace with your real Discord channel ID

def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return []
    with open(TASKS_FILE, "r") as f:
        return json.load(f)

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=4)

@app.route("/add_task", methods=["POST"])
def add_task():
    try:
        data = request.json
        task_name = data['task']
        due_date = datetime.strptime(data['due_date'], "%b %d %Y")
        num_reminders = int(data['num_reminders'])

        today = datetime.today().date()
        days_until_due = (due_date.date() - today).days
        interval = days_until_due / (num_reminders + 1)
        reminder_dates = [
            (today + timedelta(days=round(interval * (i + 1)))).isoformat()
            for i in range(num_reminders)
        ]

        tasks = load_tasks()

        new_task = {
            'user_id': None,  # or a Discord user ID if you want to tag
            'task_name': task_name,
            'due': due_date.isoformat(),
            'channel_id': DISCORD_CHANNEL_ID,
            'reminder_dates': reminder_dates,
        }

        tasks.append(new_task)
        save_tasks(tasks)

        return jsonify({"status": "success", "message": "Task added!"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == "__main__":
    app.run(debug=True)
