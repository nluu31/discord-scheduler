import os
from flask import Flask, redirect, url_for, session, request, render_template_string
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret_key')

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

# Simple homepage
@app.route('/')
def home():
    user = session.get('user')
    if user:
        return (
            f"<h1>Welcome, {user['username']}#{user['discriminator']}!</h1>"
            f"<p>Your Discord ID is: {user['id']}</p>"
            f"<a href='/dashboard'>Go to dashboard</a><br>"
            f"<a href='/logout'>Logout</a>"
        )
    return '<a href="/login">Login with Discord</a>'

# Start OAuth login
@app.route('/login')
def login():
    redirect_uri = url_for('authorize', _external=True)
    return discord.authorize_redirect(redirect_uri)

# OAuth callback handler
@app.route('/callback')
def authorize():
    token = discord.authorize_access_token()
    resp = discord.get('users/@me', token=token)
    user_info = resp.json()
    session['user'] = user_info
    return redirect('/dashboard')

# Protected dashboard
@app.route('/dashboard')
def dashboard():
    user = session.get('user')
    if not user:
        return redirect('/')
    return (
        f"<h2>Dashboard for {user['username']}#{user['discriminator']}</h2>"
        f"<p>Discord ID: {user['id']}</p>"
        f"<p>Email: {user.get('email', 'Not provided')}</p>"
        "<a href='/logout'>Logout</a>"
    )

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
