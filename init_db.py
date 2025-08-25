import sqlite3
import os

def init_database():
    db_path = os.path.join(os.path.dirname(__file__), 'tasks.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Create tasks table
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT NOT NULL,
                  completed INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Insert sample data if empty
    c.execute("SELECT COUNT(*) FROM tasks")
    if c.fetchone()[0] == 0:
        sample_tasks = [
            ('Learn Python Deployment', 0),
            ('Build Discord Bot', 0),
            ('Deploy to Render', 0)
        ]
        c.executemany("INSERT INTO tasks (title, completed) VALUES (?, ?)", sample_tasks)
    
    conn.commit()
    conn.close()
    print("Database initialized successfully!")

if __name__ == '__main__':
    init_database()