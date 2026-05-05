#!/usr/bin/env python3
"""
PhotoBoth v2.2 - Modern Photo Platform
Glassmorphism UI, Secure Auth, Admin Controls, Anti-Bruteforce
Single File - Deploy on Render.com
"""

import os, sqlite3, hashlib, secrets, re, time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, redirect, url_for, session, send_file, render_template_string, jsonify
from werkzeug.utils import secure_filename

# ============== CONFIG ==============
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PFP_FOLDER'] = 'pfp'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'webp'}
app.config['PFP_ALLOWED'] = {'png', 'jpg', 'jpeg'}
# Brute-force protection settings
app.config['MAX_LOGIN_ATTEMPTS'] = 5
app.config['LOCKOUT_MINUTES'] = 15
app.config['RATE_LIMIT_WINDOW'] = 60  # seconds

DB_PATH = os.environ.get('DATABASE_URL', 'photoboth.db').replace('sqlite:///', '')
for folder in [app.config['UPLOAD_FOLDER'], app.config['PFP_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# ============== DATABASE ==============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nickname TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'user',
        pfp_filename TEXT DEFAULT 'default.png',
        status TEXT DEFAULT 'active',
        ban_until TEXT,
        comment_banned_until TEXT,
        last_seen TEXT,
        ip_address TEXT,
        failed_logins INTEGER DEFAULT 0,
        locked_until TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        title TEXT,
        description TEXT,
        privacy TEXT DEFAULT 'public',
        uploader_id INTEGER,
        upload_date TEXT DEFAULT CURRENT_TIMESTAMP,
        views INTEGER DEFAULT 0,
        downloads INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY (uploader_id) REFERENCES users (id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS photo_access (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(photo_id, user_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (photo_id) REFERENCES photos (id),
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(photo_id, user_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        viewed INTEGER DEFAULT 0,
        downloaded INTEGER DEFAULT 0,
        last_viewed TEXT,
        UNIQUE(photo_id, user_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        guest_nick TEXT,
        guest_email TEXT,
        subject TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        admin_reply TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS ip_bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address TEXT UNIQUE NOT NULL,
        reason TEXT,
        banned_until TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address TEXT NOT NULL,
        nickname TEXT,
        attempted_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    for key, val in [("site_offline", "0"), ("maintenance_msg", "Site under maintenance")]:
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, val))
    
    # Migrations
    for sql in [
        'ALTER TABLE photos ADD COLUMN title TEXT',
        'ALTER TABLE photos ADD COLUMN description TEXT',
        'ALTER TABLE photos ADD COLUMN privacy TEXT DEFAULT "public"',
        'ALTER TABLE users ADD COLUMN pfp_filename TEXT DEFAULT "default.png"',
        'ALTER TABLE users ADD COLUMN comment_banned_until TEXT',
        'ALTER TABLE users ADD COLUMN last_seen TEXT',
        'ALTER TABLE users ADD COLUMN failed_logins INTEGER DEFAULT 0',
        'ALTER TABLE users ADD COLUMN locked_until TEXT',
        'ALTER TABLE support_tickets ADD COLUMN guest_nick TEXT',
        'ALTER TABLE support_tickets ADD COLUMN guest_email TEXT'
    ]:
        try: c.execute(sql)
        except: pass
    
    # Default accounts
    for nick, pwd, role in [('admin', 'PhotoBoth2026!', 'admin'), ('user', 'User123!', 'user')]:
        c.execute('INSERT OR IGNORE INTO users (nickname, password_hash, role) VALUES (?, ?, ?)',
                  (nick, hashlib.sha256(pwd.encode()).hexdigest(), role))
    
    # Default PFP
    if not os.path.exists(os.path.join(app.config['PFP_FOLDER'], 'default.png')):
        import base64
        default_pfp = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
        with open(os.path.join(app.config['PFP_FOLDER'], 'default.png'), 'wb') as f:
            f.write(default_pfp)
    
    conn.commit()
    conn.close()

init_db()

# ============== HELPERS ==============
def hash_pwd(p): return hashlib.sha256(p.encode()).hexdigest()
def allowed_file(fn): return '.' in fn and fn.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
def allowed_pfp(fn): return '.' in fn and fn.rsplit('.', 1)[1].lower() in app.config['PFP_ALLOWED']
def get_ip(): return request.headers.get('X-Forwarded-For', request.remote_addr)

def get_user():
    uid = session.get('user_id')
    if not uid: return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    u = conn.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    return dict(u) if u else None

def update_last_seen():
    uid = session.get('user_id')
    if uid:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('UPDATE users SET last_seen = ? WHERE id = ?', (datetime.now().isoformat(), uid))
        conn.commit()
        conn.close()

# ============== BRUTE-FORCE PROTECTION ==============
def check_rate_limit(ip):
    """Check if IP is rate-limited for login attempts"""
    conn = sqlite3.connect(DB_PATH)
    window = (datetime.now() - timedelta(seconds=app.config['RATE_LIMIT_WINDOW'])).isoformat()
    count = conn.execute('SELECT COUNT(*) FROM login_attempts WHERE ip_address = ? AND attempted_at > ?', (ip, window)).fetchone()[0]
    conn.close()
    return count >= 10  # 10 attempts per minute from same IP

def record_login_attempt(ip, nickname=None):
    """Record a login attempt for rate limiting"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT INTO login_attempts (ip_address, nickname) VALUES (?, ?)', (ip, nickname))
    # Clean old attempts
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    conn.execute('DELETE FROM login_attempts WHERE attempted_at < ?', (cutoff,))
    conn.commit()
    conn.close()

def check_account_lockout(nickname):
    """Check if account is locked due to failed attempts"""
    conn = sqlite3.connect(DB_PATH)
    u = conn.execute('SELECT failed_logins, locked_until FROM users WHERE nickname = ?', (nickname,)).fetchone()
    conn.close()
    if not u: return False, 0
    failed, locked = u[0], u[1]
    if locked and datetime.fromisoformat(locked) > datetime.now():
        return True, (datetime.fromisoformat(locked) - datetime.now()).seconds // 60
    if failed >= app.config['MAX_LOGIN_ATTEMPTS']:
        # Lock account
        until = (datetime.now() + timedelta(minutes=app.config['LOCKOUT_MINUTES'])).isoformat()
        conn = sqlite3.connect(DB_PATH)
        conn.execute('UPDATE users SET locked_until = ? WHERE nickname = ?', (until, nickname))
        conn.commit()
        conn.close()
        return True, app.config['LOCKOUT_MINUTES']
    return False, 0

def reset_login_attempts(nickname):
    """Reset failed login counter on successful login"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET failed_logins = 0, locked_until = NULL WHERE nickname = ?', (nickname,))
    conn.commit()
    conn.close()

def increment_failed_login(nickname):
    """Increment failed login counter"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET failed_logins = failed_logins + 1 WHERE nickname = ?', (nickname,))
    conn.commit()
    conn.close()

# ============== STATUS CHECKS ==============
def is_banned(u):
    if not u or u['status'] != 'active': return True
    if u['status'] == 'banned' and (not u['ban_until'] or datetime.fromisoformat(u['ban_until']) > datetime.now()):
        return True
    return False

def is_comment_banned(u):
    if not u: return False
    if u['comment_banned_until'] and datetime.fromisoformat(u['comment_banned_until']) > datetime.now():
        return True
    return False

def is_site_offline():
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute('SELECT value FROM settings WHERE key = "site_offline"').fetchone()
    conn.close()
    return r and r[0] == '1'

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============== DECORATORS ==============
def require_login(f):
    @wraps(f)
    def dec(*a, **kw):
        if session.get('user_id'):
            update_last_seen()
            return f(*a, **kw)
        return redirect(url_for('login', next=request.path))
    return dec

def require_admin(f):
    @wraps(f)
    def dec(*a, **kw):
        u = get_user()
        if u and u['role'] == 'admin':
            update_last_seen()
            return f(*a, **kw)
        return redirect(url_for('index'))
    return dec

def check_access(f):
    @wraps(f)
    def dec(*a, **kw):
        if request.method in ['HEAD', 'OPTIONS']: return f(*a, **kw)
        update_last_seen()
        if is_site_offline() and not (get_user() and get_user()['role'] == 'admin'):
            return render_template_string(OFFLINE_TMPL, msg=os.environ.get('MAINTENANCE_MSG', 'Site offline')), 503
        ip = get_ip()
        conn = sqlite3.connect(DB_PATH)
        banned = conn.execute('SELECT 1 FROM ip_bans WHERE ip_address = ? AND (banned_until IS NULL OR banned_until > ?)', (ip, datetime.now().isoformat())).fetchone()
        conn.close()
        if banned:
            return render_template_string(OFFLINE_TMPL, msg="Access restricted"), 403
        return f(*a, **kw)
    return dec

# ============== MODERN CSS ==============
CSS = '''
<style>
:root {
  --bg: #0a0e17; --bg-card: rgba(30, 35, 50, 0.7); --bg-hover: rgba(45, 52, 72, 0.9);
  --border: rgba(100, 116, 139, 0.3); --primary: #7c3aed; --primary-glow: rgba(124, 58, 237, 0.4);
  --success: #10b981; --danger: #ef4444; --warning: #f59e0b;
  --text: #f1f5f9; --text-dim: #94a3b8; --text-link: #818cf8;
  --glass: blur(12px) saturate(180%); --shadow: 0 8px 32px rgba(0,0,0,0.4);
  --radius: 16px; --transition: 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: radial-gradient(1200px 600px at 10% -10%, rgba(124,58,237,0.15), transparent),
              radial-gradient(800px 400px at 90% 10%, rgba(16,185,129,0.1), transparent),
              var(--bg);
  color: var(--text); min-height: 100vh; line-height: 1.6;
  background-attachment: fixed;
}
@keyframes pulse-glow { 0%,100%{box-shadow:0 0 20px var(--primary-glow)} 50%{box-shadow:0 0 35px var(--primary-glow)} }
@keyframes slide-in { from{opacity:0;transform:translateY(12px)} to{opacity:1;transform:translateY(0)} }
.container { max-width: 1400px; margin: 0 auto; padding: 0 20px; }
header {
  background: var(--bg-card); backdrop-filter: var(--glass);
  border-bottom: 1px solid var(--border); padding: 14px 0; position: sticky; top: 0; z-index: 100;
  box-shadow: var(--shadow);
}
.nav { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.logo {
  font-size: 22px; font-weight: 800; background: linear-gradient(135deg, #818cf8, #7c3aed, #10b981);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  animation: pulse-glow 4s infinite;
}
.nav-links { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.nav-links a {
  color: var(--text-dim); text-decoration: none; font-size: 14px; padding: 8px 14px;
  border-radius: 10px; transition: var(--transition);
}
.nav-links a:hover { color: var(--text); background: var(--bg-hover); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 10px 20px; border: 1px solid var(--border); border-radius: 12px;
  background: var(--bg-card); color: var(--text); cursor: pointer; font-size: 14px; font-weight: 500;
  transition: var(--transition); backdrop-filter: var(--glass);
}
.btn:hover { background: var(--bg-hover); transform: translateY(-2px); }
.btn:active { transform: scale(0.98); }
.btn-primary {
  background: linear-gradient(135deg, var(--primary), #6d28d9);
  border-color: rgba(124,58,237,0.5); color: white; box-shadow: 0 4px 14px var(--primary-glow);
}
.btn-primary:hover { box-shadow: 0 6px 20px var(--primary-glow); }
.btn-danger { background: linear-gradient(135deg, var(--danger), #dc2626); border-color: rgba(239,68,68,0.4); color: white; }
.btn-success { background: linear-gradient(135deg, var(--success), #059669); border-color: rgba(16,185,129,0.4); color: white; }
.btn-warning { background: linear-gradient(135deg, var(--warning), #d97706); border-color: rgba(245,158,11,0.4); color: white; }
.card {
  background: var(--bg-card); backdrop-filter: var(--glass);
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px; margin-bottom: 20px; box-shadow: var(--shadow);
  animation: slide-in 0.4s ease;
}
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; margin: 24px 0; }
.photo-card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden; transition: var(--transition); position: relative;
}
.photo-card:hover { border-color: var(--primary); transform: translateY(-4px); box-shadow: 0 12px 40px rgba(0,0,0,0.5); }
.photo-badge {
  position: absolute; top: 12px; right: 12px; padding: 4px 10px; border-radius: 20px;
  font-size: 11px; font-weight: 600; backdrop-filter: blur(8px);
}
.badge-private { background: rgba(245,158,11,0.2); color: var(--warning); border: 1px solid rgba(245,158,11,0.3); }
.photo-img { width: 100%; height: 200px; object-fit: cover; background: linear-gradient(135deg, #1e293b, #334155); }
.photo-meta { padding: 16px; }
.photo-title { font-weight: 700; margin-bottom: 6px; font-size: 16px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.photo-desc { color: var(--text-dim); font-size: 13px; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.stats { display: flex; gap: 14px; color: var(--text-dim); font-size: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.stats span { display: flex; align-items: center; gap: 4px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.actions .btn { flex: 1; min-width: 70px; padding: 8px 12px; font-size: 13px; }
.input-group { margin-bottom: 14px; }
.input-group label { display: block; font-size: 13px; color: var(--text-dim); margin-bottom: 6px; font-weight: 500; }
.input-group input, .input-group textarea, .input-group select {
  width: 100%; padding: 12px 14px; background: rgba(15,23,42,0.6);
  border: 1px solid var(--border); border-radius: 10px; color: var(--text);
  font-size: 14px; transition: var(--transition);
}
.input-group input:focus, .input-group textarea:focus, .input-group select:focus {
  outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow);
}
.modal {
  display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  background: rgba(0,0,0,0.7); z-index: 200; align-items: center; justify-content: center;
  backdrop-filter: blur(4px); padding: 20px;
}
.modal.active { display: flex; animation: slide-in 0.3s ease; }
.modal-box {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
  width: 100%; max-width: 480px; max-height: 90vh; overflow: auto; padding: 24px;
  box-shadow: var(--shadow); animation: slide-in 0.3s ease;
}
.modal-header { font-size: 20px; font-weight: 700; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }
.error-msg { color: var(--danger); font-size: 13px; margin-top: 6px; display: block; }
.success-msg { color: var(--success); font-size: 13px; margin-top: 6px; display: block; }
.comments-section { border-top: 1px solid var(--border); padding-top: 16px; margin-top: 16px; }
.comment {
  display: flex; gap: 12px; padding: 12px 0; border-bottom: 1px dashed var(--border);
  animation: slide-in 0.3s ease;
}
.comment:last-child { border-bottom: none; }
.comment-avatar {
  width: 36px; height: 36px; border-radius: 50%; object-fit: cover;
  border: 2px solid var(--border); flex-shrink: 0;
}
.comment-body { flex: 1; min-width: 0; }
.comment-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.comment-author { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.verified-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: white; font-size: 10px; font-weight: 700;
}
.comment-time { color: var(--text-dim); font-size: 11px; }
.comment-text { font-size: 14px; line-height: 1.5; word-wrap: break-word; }
.load-more {
  color: var(--text-link); font-size: 13px; cursor: pointer; margin-top: 8px;
  background: none; border: none; padding: 6px 0; transition: var(--transition);
}
.load-more:hover { color: var(--primary); }
.sidebar {
  position: fixed; right: 0; top: 70px; width: 280px; height: calc(100vh - 70px);
  background: var(--bg-card); backdrop-filter: var(--glass);
  border-left: 1px solid var(--border); padding: 20px; overflow-y: auto;
  box-shadow: -4px 0 20px rgba(0,0,0,0.3); z-index: 90;
}
.sidebar-title { font-size: 15px; font-weight: 700; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
.online-list { display: flex; flex-direction: column; gap: 10px; }
.online-user {
  display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  border-radius: 10px; transition: var(--transition); cursor: pointer;
}
.online-user:hover { background: var(--bg-hover); }
.online-avatar {
  width: 32px; height: 32px; border-radius: 50%; object-fit: cover;
  border: 2px solid var(--success); position: relative;
}
.online-avatar::after {
  content: ''; position: absolute; bottom: 2px; right: 2px;
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--success); border: 2px solid var(--bg-card);
}
.online-name { font-size: 13px; font-weight: 500; flex: 1; }
.online-role { font-size: 10px; padding: 2px 8px; border-radius: 10px; background: var(--primary); color: white; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th, .table td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
.table th { color: var(--text-dim); font-weight: 600; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-open { background: rgba(59,130,246,0.15); color: #60a5fa; }
.badge-resolved { background: rgba(16,185,129,0.15); color: #34d399; }
.badge-admin { background: linear-gradient(135deg, var(--primary), #6d28d9); color: white; }
.badge-banned { background: rgba(239,68,68,0.15); color: #f87171; }
.admin-panel { display: grid; grid-template-columns: 240px 1fr; gap: 24px; margin-top: 24px; }
.admin-nav a {
  display: block; padding: 12px 16px; color: var(--text-dim); border-radius: 10px;
  margin-bottom: 6px; transition: var(--transition); font-weight: 500;
}
.admin-nav a:hover, .admin-nav a.active { background: var(--bg-hover); color: var(--text); }
.offline-msg { text-align: center; padding: 100px 20px; font-size: 18px; color: var(--text-dim); }
.pfp-upload { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }
.pfp-preview {
  width: 64px; height: 64px; border-radius: 50%; object-fit: cover;
  border: 3px solid var(--primary); box-shadow: 0 4px 14px var(--primary-glow);
}
.main-content { margin-right: 300px; }
.ticket-message {
  background: rgba(15,23,42,0.4); padding: 12px; border-radius: 8px; margin: 8px 0;
  font-size: 13px; white-space: pre-wrap; max-height: 150px; overflow-y: auto;
}
.admin-reply {
  background: rgba(124,58,237,0.1); padding: 12px; border-radius: 8px; margin: 8px 0;
  font-size: 13px; border-left: 3px solid var(--primary);
}
@media (max-width: 1100px) {
  .sidebar { display: none; }
  .main-content { margin-right: 0; }
  .grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 768px) {
  .container { padding: 0 12px; }
  .nav { flex-wrap: wrap; }
  .nav-links { width: 100%; justify-content: center; margin-top: 10px; }
  .grid { grid-template-columns: 1fr; }
  .photo-img { height: 220px; }
  .admin-panel { grid-template-columns: 1fr; }
  .admin-nav { display: flex; overflow-x: auto; padding-bottom: 10px; gap: 6px; }
  .admin-nav a { white-space: nowrap; padding: 10px 14px; }
  .btn { width: 100%; margin-bottom: 8px; }
  .actions .btn { min-width: auto; }
  input, textarea, select, button { font-size: 16px !important; }
  .modal-box { margin: 10px; }
}
</style>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
'''

JS = '''
<script>
function showModal(id){document.getElementById(id).classList.add('active');}
function hideModal(id){document.getElementById(id).classList.remove('active');}
async function likePhoto(id){
  const r=await fetch('/api/like/'+id,{method:'POST'});const d=await r.json();
  if(d.ok){document.getElementById('likes-'+id).textContent=d.likes;}
  else{alert(d.error);}
}
function trackDownload(id,file){fetch('/api/download/'+id,{method:'POST'});setTimeout(()=>window.location.href='/uploads/'+file,100);}
async function trackView(id){await fetch('/api/view/'+id,{method:'POST'});}
function toggleComments(id){
  const s=document.getElementById('comments-'+id),b=document.getElementById('btn-comments-'+id);
  if(s.style.display==='none'){s.style.display='block';b.textContent='Hide Comments';}
  else{s.style.display='none';b.textContent='Show Comments';}
}
document.querySelectorAll('form').forEach(f=>{
  f.addEventListener('submit',e=>{
    const fi=f.querySelector('input[type="file"]');
    if(fi&&fi.files[0]){
      const ext=fi.files[0].name.split('.').pop().toLowerCase();
      if(!['png','jpg','jpeg','webp'].includes(ext)){e.preventDefault();alert('Allowed: PNG, JPG, JPEG, WEBP');}
    }
  });
});
document.querySelectorAll('.modal').forEach(m=>m.addEventListener('click',e=>{if(e.target===m)m.classList.remove('active');}));
setInterval(()=>{fetch('/api/online').then(r=>r.text()).then(d=>{if(document.getElementById('online-list'))document.getElementById('online-list').innerHTML=d;});},30000);
</script>
'''

OFFLINE_TMPL = '<!DOCTYPE html><html><head><title>PhotoBoth</title>'+CSS+'</head><body><div class="offline-msg"><h1 style="font-size:28px;margin-bottom:16px">🔧 {% if msg %}{{ msg }}{% else %}Site Offline{% endif %}</h1><p style="color:var(--text-dim)">We will return shortly.</p></div></body></html>'

LOGIN_TMPL = '''<!DOCTYPE html><html><head><title>Login</title><meta name="viewport" content="width=device-width,initial-scale=1">'''+CSS+'''</head><body>
<div class="container" style="max-width:440px;margin:80px auto">
  <div class="modal-box" style="margin:0">
    <h2 class="modal-header">Welcome Back</h2>
    <form method="POST">
      <div class="input-group"><label>Nickname</label><input type="text" name="nickname" required autocomplete="username"></div>
      <div class="input-group"><label>Password</label><input type="password" name="password" required autocomplete="current-password"></div>
      {% if error %}<span class="error-msg">{{ error }}</span>{% endif %}
      <button type="submit" class="btn btn-primary" style="width:100%;margin-top:16px">Sign In</button>
    </form>
    <p style="margin-top:20px;font-size:13px;color:var(--text-dim);text-align:center">
      New here? <a href="/register" style="color:var(--text-link)">Create account</a> • 
      <a href="/support" style="color:var(--text-link)">Need help?</a>
    </p>
  </div>
</div></body></html>'''

REGISTER_TMPL = '''<!DOCTYPE html><html><head><title>Register</title><meta name="viewport" content="width=device-width,initial-scale=1">'''+CSS+'''</head><body>
<div class="container" style="max-width:440px;margin:80px auto">
  <div class="modal-box" style="margin:0">
    <h2 class="modal-header">Create Account</h2>
    <form method="POST" enctype="multipart/form-data">
      <div class="input-group"><label>Nickname</label><input type="text" name="nickname" required pattern="[a-zA-Z0-9_]{3,20}" autocomplete="username"></div>
      <div class="input-group"><label>Password</label><input type="password" name="password" required minlength="6" autocomplete="new-password"></div>
      <div class="input-group"><label>Confirm</label><input type="password" name="confirm" required autocomplete="new-password"></div>
      <div class="input-group"><label>Profile Picture (optional)</label><input type="file" name="pfp" accept=".png,.jpg,.jpeg"></div>
      {% if error %}<span class="error-msg">{{ error }}</span>{% endif %}
      <button type="submit" class="btn btn-primary" style="width:100%;margin-top:16px">Register</button>
    </form>
    <p style="margin-top:20px;font-size:13px;color:var(--text-dim);text-align:center">Have an account? <a href="/login" style="color:var(--text-link)">Sign In</a></p>
  </div>
</div></body></html>'''

MAIN_TMPL = '''<!DOCTYPE html><html><head><title>PhotoBoth</title><meta name="viewport" content="width=device-width,initial-scale=1">'''+CSS+'''</head><body>
<header><div class="container nav">
  <a href="/" class="logo">✨ PhotoBoth</a>
  <div class="nav-links">
    <a href="/support">Support</a>
    {% if user and user.role=='admin' %}<a href="/admin">Dashboard</a>{% endif %}
    {% if user %}
      <div style="display:flex;align-items:center;gap:8px">
        <img src="/pfp/{{ user.pfp_filename }}" class="online-avatar" style="width:28px;height:28px;border:none">
        <span style="font-size:13px">{{ user.nickname }}</span>
      </div>
      <a href="/profile" class="btn">Profile</a>
      <a href="/logout" class="btn btn-danger">Sign Out</a>
    {% else %}
      <a href="/login" class="btn btn-primary">Sign In</a>
    {% endif %}
  </div>
</div></header>

<div class="container" style="display:flex;gap:24px">
  <div class="main-content" style="flex:1">
    {% if user and user.role=='admin' %}
    <div class="card">
      <h3 style="margin-bottom:16px;font-size:18px">📤 Upload Image</h3>
      <form method="POST" action="/upload" enctype="multipart/form-data">
        <div class="input-group"><label>Image File</label><input type="file" name="file" accept=".png,.jpg,.jpeg,.webp" required></div>
        <div class="input-group"><label>Title</label><input type="text" name="title" placeholder="Give your image a title..." maxlength="100"></div>
        <div class="input-group"><label>Description</label><textarea name="desc" rows="2" placeholder="Add a description..." maxlength="300"></textarea></div>
        <div class="input-group"><label>Privacy</label>
          <select name="privacy">
            <option value="public">🌐 Public - Everyone can see</option>
            <option value="private">🔒 Private - Only selected users</option>
          </select>
        </div>
        <div id="private-users" class="input-group" style="display:none">
          <label>Allow these users (comma-separated nicknames)</label>
          <input type="text" name="allowed_users" placeholder="user1, user2, admin">
        </div>
        <button type="submit" class="btn btn-primary">Upload</button>
      </form>
    </div>
    <script>document.querySelector('select[name="privacy"]').addEventListener('change',e=>{document.getElementById('private-users').style.display=e.target.value==='private'?'block':'none';});</script>
    {% endif %}

    <div class="grid">
      {% for photo in photos %}
      <div class="photo-card" data-id="{{ photo.id }}">
        {% if photo.privacy=='private' %}<span class="photo-badge badge-private">🔒 Private</span>{% endif %}
        <div class="photo-img" style="background-image:url('/uploads/{{ photo.filename }}');background-size:cover;background-position:center"></div>
        <div class="photo-meta">
          <div class="photo-title">{{ photo.title or photo.original_name }}</div>
          {% if photo.description %}<div class="photo-desc">{{ photo.description }}</div>{% endif %}
          <div class="stats">
            <span>👁️ <span id="views-{{ photo.id }}">{{ photo.views }}</span></span>
            <span>⬇️ <span id="downloads-{{ photo.id }}">{{ photo.downloads }}</span></span>
            <span>❤️ <span id="likes-{{ photo.id }}">{{ photo.likes }}</span></span>
          </div>
          <div class="actions">
            {% if user %}
              <button class="btn" onclick="likePhoto({{ photo.id }})">Like</button>
              <button class="btn" onclick="trackDownload({{ photo.id }}, '{{ photo.filename }}')">Download</button>
            {% else %}
              <a href="/login" class="btn" style="flex:1">Sign in to interact</a>
            {% endif %}
          </div>
          <div class="comments-section">
            <div id="comments-{{ photo.id }}" style="display:none">
              {% for c in photo.comments %}
              <div class="comment">
                <img src="/pfp/{{ c.pfp }}" class="comment-avatar" alt="">
                <div class="comment-body">
                  <div class="comment-header">
                    <span class="comment-author">
                      {{ c.nick }}
                      {% if c.is_admin %}<span class="verified-badge" title="Verified Admin">✓</span>{% endif %}
                    </span>
                    <span class="comment-time">{{ c.created_at[:10] }}</span>
                  </div>
                  <div class="comment-text">{{ c.content }}</div>
                </div>
              </div>
              {% endfor %}
              {% if photo.comments|length < photo.comment_count %}<div style="color:var(--text-dim);font-size:12px;margin-top:6px">+ {{ photo.comment_count - photo.comments|length }} more</div>{% endif %}
            </div>
            <button class="load-more" id="btn-comments-{{ photo.id }}" onclick="toggleComments({{ photo.id }})">Show Comments</button>
            {% if user and not user.comment_banned %}
            <form method="POST" action="/comment/{{ photo.id }}" style="margin-top:12px;display:flex;gap:8px">
              <input type="text" name="content" placeholder="Add a comment..." required style="flex:1">
              <button type="submit" class="btn">Post</button>
            </form>
            {% elif user and user.comment_banned %}
            <p style="font-size:12px;color:var(--warning);margin-top:8px">⚠️ Commenting restricted</p>
            {% endif %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- Online Users Sidebar -->
  <aside class="sidebar">
    <div class="sidebar-title">🟢 Online Now ({{ online_count }})</div>
    <div class="online-list" id="online-list">
      {% for u in online_users %}
      <div class="online-user">
        <img src="/pfp/{{ u.pfp }}" class="online-avatar" alt="">
        <span class="online-name">{{ u.nick }}</span>
        {% if u.role=='admin' %}<span class="online-role">ADMIN</span>{% endif %}
      </div>
      {% endfor %}
    </div>
  </aside>
</div>
'''+JS+'''<script>document.querySelectorAll('.photo-card').forEach(c=>{if(c.dataset.id)trackView(c.dataset.id);});</script>
</body></html>'''

ADMIN_TMPL = '''<!DOCTYPE html><html><head><title>Admin</title><meta name="viewport" content="width=device-width,initial-scale=1">'''+CSS+'''</head><body>
<header><div class="container nav"><a href="/" class="logo">⚙️ Admin Panel</a><div class="nav-links"><a href="/">Site</a><a href="/logout">Sign Out</a></div></div></header>
<div class="container admin-panel">
  <nav class="admin-nav card">
    <a href="#photos" class="active">📸 Photos</a>
    <a href="#users">👥 Users</a>
    <a href="#banned">🚫 Banned</a>
    <a href="#tickets">🎫 Tickets</a>
    <a href="#settings">⚙️ Settings</a>
  </nav>
  <div>
    <section id="photos" class="card">
      <h3>Manage Photos</h3>
      <table class="table"><thead><tr><th>Image</th><th>Title</th><th>Privacy</th><th>Stats</th><th>Actions</th></tr></thead><tbody>
      {% for p in photos %}<tr><td><img src="/uploads/{{ p.filename }}" style="width:50px;height:50px;object-fit:cover;border-radius:8px"></td>
      <td>{{ p.title or p.original_name }}</td><td><span class="badge {% if p.privacy=='private' %}badge-open{% else %}badge-resolved{% endif %}">{{ p.privacy }}</span></td>
      <td>{{ p.views }}v/{{ p.downloads }}d/{{ p.likes }}l</td>
      <td><form method="POST" action="/admin/photo/{{ p.id }}/delete" onsubmit="return confirm('Delete?')"><button class="btn btn-danger">Delete</button></form></td></tr>{% endfor %}</tbody></table>
    </section>
    
    <section id="users" class="card">
      <h3>User Management</h3>
      <table class="table"><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Controls</th></tr></thead><tbody>
      {% for u in users %}<tr><td><div style="display:flex;align-items:center;gap:10px"><img src="/pfp/{{ u.pfp }}" style="width:32px;height:32px;border-radius:50%">{{ u.nickname }}</div></td>
      <td>{% if u.role=='admin' %}<span class="badge badge-admin">ADMIN</span>{% else %}User{% endif %}</td>
      <td><span class="badge {% if u.status=='active' %}badge-resolved{% else %}badge-banned{% endif %}">{{ u.status }}</span></td>
      <td style="display:flex;gap:6px;flex-wrap:wrap">
        {% if u.role!='admin' %}
        <form method="POST" action="/admin/user/{{ u.id }}/ban"><button class="btn btn-danger">Ban</button></form>
        <form method="POST" action="/admin/user/{{ u.id }}/timeout"><button class="btn btn-warning">24h</button></form>
        <form method="POST" action="/admin/user/{{ u.id }}/comment-ban"><button class="btn" style="background:var(--text-dim)">No Comments</button></form>
        <form method="POST" action="/admin/user/{{ u.id }}/promote"><button class="btn btn-success">Promote</button></form>
        {% endif %}
      </td></tr>{% endfor %}</tbody></table>
    </section>
    
    <!-- NEW: Banned Users Management -->
    <section id="banned" class="card">
      <h3>🚫 Banned Users</h3>
      {% if banned_users %}
      <table class="table"><thead><tr><th>User</th><th>Banned Until</th><th>Reason</th><th>Action</th></tr></thead><tbody>
      {% for u in banned_users %}<tr><td>{{ u.nickname }}</td>
      <td>{% if u.ban_until %}{{ u.ban_until[:16] }}{% else %}Permanent{% endif %}</td>
      <td>{{ u.ban_reason or 'Admin action' }}</td>
      <td><form method="POST" action="/admin/user/{{ u.id }}/unban"><button class="btn btn-success">Unban</button></form></td></tr>{% endfor %}</tbody></table>
      {% else %}
      <p style="color:var(--text-dim);padding:20px;text-align:center">No banned users.</p>
      {% endif %}
    </section>
    
    <section id="tickets" class="card">
      <h3>Support Tickets</h3>
      {% if tickets %}
      <table class="table"><thead><tr><th>User</th><th>Subject</th><th>Status</th><th>Message</th><th>Reply</th></tr></thead><tbody>
      {% for t in tickets %}<tr><td>{{ t.nick or t.guest_nick }}</td><td>{{ t.subject }}</td><td><span class="badge badge-{{ t.status }}">{{ t.status }}</span></td>
      <td><div class="ticket-message">{{ t.message }}</div>{% if t.guest_email %}<small style="color:var(--text-dim)">📧 {{ t.guest_email }}</small>{% endif %}</td>
      <td>
        <form method="POST" action="/admin/ticket/{{ t.id }}/reply" style="display:flex;flex-direction:column;gap:6px">
          <textarea name="reply" placeholder="Admin response..." required style="min-height:60px;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-canvas);color:var(--text)"></textarea>
          <button class="btn btn-primary">Send Reply</button>
        </form>
        {% if t.admin_reply %}<div class="admin-reply"><strong>Admin:</strong> {{ t.admin_reply }}</div>{% endif %}
      </td></tr>{% endfor %}</tbody></table>
      {% else %}
      <p style="color:var(--text-dim);padding:20px;text-align:center">No open tickets.</p>
      {% endif %}
    </section>
    
    <section id="settings" class="card">
      <h3>Site Controls</h3>
      <div style="display:grid;gap:16px">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:16px;background:rgba(124,58,237,0.1);border-radius:12px">
          <div><strong>🔌 Site Status</strong><p style="color:var(--text-dim);font-size:13px">Toggle maintenance mode</p></div>
          <form method="POST" action="/admin/toggle-offline"><button class="btn {% if offline %}btn-primary{% else %}btn-danger{% endif %}">{% if offline %}Bring Online{% else %}Take Offline{% endif %}</button></form>
        </div>
        <div class="input-group"><label>Maintenance Message</label><form method="POST" action="/admin/set-maintenance"><textarea name="msg" rows="2" style="width:100%">{{ maint_msg }}</textarea><button class="btn btn-primary" style="margin-top:8px">Update</button></form></div>
      </div>
    </section>
  </div>
</div></body></html>'''

SUPPORT_TMPL = '''<!DOCTYPE html><html><head><title>Support</title><meta name="viewport" content="width=device-width,initial-scale=1">'''+CSS+'''</head><body>
<div class="container" style="max-width:640px;margin:60px auto">
  <div class="card">
    <h2 style="margin-bottom:20px;font-size:22px">🎫 Support Center</h2>
    {% if not user %}
    <div class="input-group"><label>Your Nickname (optional)</label><input type="text" name="guest_nick" id="guest_nick"></div>
    <div class="input-group"><label>Contact Email (for password reset)</label><input type="email" name="email"></div>
    {% endif %}
    <div class="input-group"><label>Subject</label><select name="subject">
      <option value="Password Reset">🔐 Password Reset</option>
      <option value="Account Issue">⚠️ Account Issue</option>
      <option value="Bug Report">🐛 Bug Report</option>
      <option value="Feature Request">💡 Feature Request</option>
      <option value="Other">❓ Other</option>
    </select></div>
    <div class="input-group"><label>Message</label><textarea name="message" rows="5" placeholder="Describe your issue in detail..." required></textarea></div>
    {% if error %}<span class="error-msg">{{ error }}</span>{% endif %}
    {% if success %}<span class="success-msg">✓ Ticket submitted! An admin will review and respond within the dashboard.</span>{% endif %}
    <form method="POST"><button type="submit" class="btn btn-primary" style="width:100%;margin-top:16px">Submit Ticket</button></form>
    <p style="margin-top:20px;font-size:12px;color:var(--text-dim);text-align:center">
      Tickets are managed internally. Admins will respond via the admin panel.
    </p>
  </div>
</div></body></html>'''

PROFILE_TMPL = '''<!DOCTYPE html><html><head><title>Profile</title><meta name="viewport" content="width=device-width,initial-scale=1">'''+CSS+'''</head><body>
<header><div class="container nav"><a href="/" class="logo">✨ PhotoBoth</a><div class="nav-links"><a href="/">Home</a><a href="/logout" class="btn btn-danger">Sign Out</a></div></div></header>
<div class="container" style="max-width:600px;margin:60px auto">
  <div class="card">
    <h2 style="margin-bottom:24px">👤 Your Profile</h2>
    <div class="pfp-upload">
      <img src="/pfp/{{ user.pfp_filename }}" class="pfp-preview" alt="Profile">
      <form method="POST" enctype="multipart/form-data" style="flex:1">
        <div class="input-group"><label>Change Profile Picture</label><input type="file" name="pfp" accept=".png,.jpg,.jpeg"></div>
        <button type="submit" class="btn btn-primary">Update</button>
      </form>
    </div>
    <div class="input-group"><label>Nickname</label><input type="text" value="{{ user.nickname }}" disabled style="background:rgba(15,23,42,0.3)"></div>
    <div class="input-group"><label>Role</label><input type="text" value="{{ user.role|upper }}" disabled style="background:rgba(15,23,42,0.3)"></div>
    <div class="input-group"><label>Member Since</label><input type="text" value="{{ user.created_at[:10] }}" disabled style="background:rgba(15,23,42,0.3)"></div>
    {% if user.comment_banned_until %}
    <p style="color:var(--warning);font-size:13px;margin-top:12px">⚠️ Commenting restricted until: {{ user.comment_banned_until[:10] }}</p>
    {% endif %}
  </div>
</div></body></html>'''

# ============== ROUTES ==============
@app.route('/')
@check_access
def index():
    user = get_user()
    db = db_conn()
    
    if user and user['role'] == 'admin':
        photos = db.execute('SELECT * FROM photos WHERE is_active=1 ORDER BY upload_date DESC').fetchall()
    elif user:
        photos = db.execute('''SELECT p.* FROM photos p 
            LEFT JOIN photo_access pa ON p.id=pa.photo_id AND pa.user_id=? 
            WHERE p.is_active=1 AND (p.privacy='public' OR pa.user_id=?) ORDER BY p.upload_date DESC''', (user['id'], user['id'])).fetchall()
    else:
        photos = db.execute('SELECT * FROM photos WHERE is_active=1 AND privacy="public" ORDER BY upload_date DESC').fetchall()
    
    photo_list = []
    for p in photos:
        pd = dict(p)
        comments = db.execute('''SELECT c.content, c.created_at, u.nickname as nick, u.pfp_filename as pfp, u.role 
            FROM comments c JOIN users u ON c.user_id=u.id 
            WHERE c.photo_id=? ORDER BY c.created_at DESC LIMIT 3''', (p['id'],)).fetchall()
        pd['comments'] = [dict(c, is_admin=dict(c)['role']=='admin') for c in comments]
        pd['comment_count'] = db.execute('SELECT COUNT(*) FROM comments WHERE photo_id=?', (p['id'],)).fetchone()[0]
        photo_list.append(pd)
    
    cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
    online = db.execute('''SELECT nickname as nick, pfp_filename as pfp, role FROM users 
        WHERE last_seen > ? ORDER BY last_seen DESC LIMIT 20''', (cutoff,)).fetchall()
    
    db.close()
    return render_template_string(MAIN_TMPL, user=user, photos=photo_list, 
                                  online_users=[dict(o) for o in online], 
                                  online_count=len(online))

@app.route('/api/online')
def api_online():
    db = db_conn()
    cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
    online = db.execute('''SELECT nickname as nick, pfp_filename as pfp, role FROM users 
        WHERE last_seen > ? ORDER BY last_seen DESC LIMIT 20''', (cutoff,)).fetchall()
    db.close()
    html = ''.join([f'<div class="online-user"><img src="/pfp/{o["pfp"]}" class="online-avatar"><span class="online-name">{o["nick"]}</span>{f"<span class=\"online-role\">ADMIN</span>" if o["role"]=="admin" else ""}</div>' for o in online])
    return html

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nick = request.form['nickname'].strip()
        pwd, conf = request.form['password'], request.form['confirm']
        if not re.match(r'^[a-zA-Z0-9_]{3,20}$', nick):
            return render_template_string(REGISTER_TMPL, error="Nickname: 3-20 chars, alphanumeric only"), 400
        if len(pwd) < 6:
            return render_template_string(REGISTER_TMPL, error="Password must be at least 6 characters"), 400
        if pwd != conf:
            return render_template_string(REGISTER_TMPL, error="Passwords do not match"), 400
        pfp = request.files.get('pfp')
        pfp_fn = 'default.png'
        if pfp and allowed_pfp(pfp.filename):
            pfp_fn = secure_filename(f"{secrets.token_hex(4)}_{pfp.filename}")
            pfp.save(os.path.join(app.config['PFP_FOLDER'], pfp_fn))
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute('INSERT INTO users (nickname, password_hash, ip_address, pfp_filename) VALUES (?, ?, ?, ?)', 
                        (nick, hash_pwd(pwd), get_ip(), pfp_fn))
            conn.commit()
            conn.close()
            return redirect(url_for('login'))
        except:
            return render_template_string(REGISTER_TMPL, error="Nickname already taken"), 400
    return render_template_string(REGISTER_TMPL)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nick, pwd = request.form['nickname'].strip(), request.form['password']
        ip = get_ip()
        
        # ✅ Brute-force: Check IP rate limit
        if check_rate_limit(ip):
            return render_template_string(LOGIN_TMPL, error="Too many attempts. Please wait."), 429
        
        # ✅ Brute-force: Check account lockout
        locked, mins = check_account_lockout(nick)
        if locked:
            return render_template_string(LOGIN_TMPL, error=f"Account locked. Try again in {mins} minutes."), 403
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        u = conn.execute('SELECT * FROM users WHERE nickname = ?', (nick,)).fetchone()
        conn.close()
        
        if not u or dict(u)['password_hash'] != hash_pwd(pwd):
            # Record failed attempt
            record_login_attempt(ip, nick)
            increment_failed_login(nick)
            return render_template_string(LOGIN_TMPL, error="Invalid credentials."), 401
        
        user = dict(u)
        if is_banned(user):
            return render_template_string(LOGIN_TMPL, error="Account is restricted."), 403
        
        # ✅ Reset failed attempts on success
        reset_login_attempts(nick)
        session['user_id'] = user['id']
        session['role'] = user['role']
        update_last_seen()
        return redirect(request.args.get('next', url_for('index')))
    return render_template_string(LOGIN_TMPL)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET', 'POST'])
@require_login
def profile():
    user = get_user()
    if request.method == 'POST':
        pfp = request.files.get('pfp')
        if pfp and allowed_pfp(pfp.filename):
            if user['pfp_filename'] != 'default.png':
                try: os.remove(os.path.join(app.config['PFP_FOLDER'], user['pfp_filename']))
                except: pass
            pfp_fn = secure_filename(f"{secrets.token_hex(4)}_{pfp.filename}")
            pfp.save(os.path.join(app.config['PFP_FOLDER'], pfp_fn))
            conn = sqlite3.connect(DB_PATH)
            conn.execute('UPDATE users SET pfp_filename = ? WHERE id = ?', (pfp_fn, user['id']))
            conn.commit()
            conn.close()
            return redirect(url_for('profile'))
    return render_template_string(PROFILE_TMPL, user=user)

@app.route('/pfp/<filename>')
def serve_pfp(filename):
    return send_file(os.path.join(app.config['PFP_FOLDER'], secure_filename(filename)))

@app.route('/upload', methods=['POST'])
@check_access
@require_admin
def upload():
    f = request.files.get('file')
    if not f or not allowed_file(f.filename):
        return redirect(url_for('index'))
    fn = secure_filename(f"{secrets.token_hex(8)}_{f.filename}")
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
    title = request.form.get('title', '').strip()[:100]
    desc = request.form.get('desc', '').strip()[:300]
    privacy = request.form.get('privacy', 'public')
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT INTO photos (filename, original_name, title, description, privacy, uploader_id) VALUES (?, ?, ?, ?, ?, ?)', 
                (fn, f.filename, title or f.filename, desc, privacy, session['user_id']))
    pid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    if privacy == 'private':
        allowed = request.form.get('allowed_users', '')
        for nick in [n.strip() for n in allowed.split(',') if n.strip()]:
            u = conn.execute('SELECT id FROM users WHERE nickname = ?', (nick,)).fetchone()
            if u:
                conn.execute('INSERT OR IGNORE INTO photo_access (photo_id, user_id) VALUES (?, ?)', (pid, u[0]))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/uploads/<filename>')
@check_access
def serve_upload(filename):
    user = get_user()
    conn = sqlite3.connect(DB_PATH)
    photo = conn.execute('SELECT privacy FROM photos WHERE filename = ?', (filename,)).fetchone()
    if photo and photo[0] == 'private':
        if not user:
            conn.close()
            return redirect(url_for('login'))
        has_access = conn.execute('SELECT 1 FROM photo_access pa JOIN photos p ON pa.photo_id=p.id WHERE p.filename=? AND (pa.user_id=? OR p.uploader_id=?)', 
                                 (filename, user['id'], user['id'])).fetchone()
        if not has_access:
            conn.close()
            return jsonify({'error': 'Access denied'}), 403
    conn.close()
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename)))

@app.route('/api/like/<int:pid>', methods=['POST'])
@require_login
def api_like(pid):
    uid = session['user_id']
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('INSERT INTO user_likes (photo_id, user_id) VALUES (?, ?)', (pid, uid))
        conn.execute('UPDATE photos SET likes = likes + 1 WHERE id = ?', (pid,))
        conn.commit()
        likes = conn.execute('SELECT likes FROM photos WHERE id = ?', (pid,)).fetchone()[0]
        return jsonify({'ok': True, 'likes': likes})
    except:
        return jsonify({'error': 'Already liked'}), 400
    finally: conn.close()

@app.route('/api/view/<int:pid>', methods=['POST'])
def api_view(pid):
    uid = session.get('user_id')
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE photos SET views = views + 1 WHERE id = ?', (pid,))
    if uid:
        conn.execute('''INSERT INTO user_states (photo_id, user_id, viewed, last_viewed) VALUES (?, ?, 1, ?) 
                       ON CONFLICT(photo_id, user_id) DO UPDATE SET viewed=1, last_viewed=?''', 
                     (pid, uid, datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/download/<int:pid>', methods=['POST'])
@require_login
def api_download(pid):
    uid = session['user_id']
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE photos SET downloads = downloads + 1 WHERE id = ?', (pid,))
    conn.execute('''INSERT INTO user_states (photo_id, user_id, downloaded) VALUES (?, ?, 1) 
                   ON CONFLICT(photo_id, user_id) DO UPDATE SET downloaded=1''', (pid, uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/comment/<int:pid>', methods=['POST'])
@require_login
def add_comment(pid):
    user = get_user()
    if is_comment_banned(user):
        return redirect(url_for('index'))
    content = request.form.get('content', '').strip()
    if len(content) < 2:
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT INTO comments (photo_id, user_id, content) VALUES (?, ?, ?)', (pid, user['id'], content))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/support', methods=['GET', 'POST'])
@check_access
def support():
    if request.method == 'POST':
        user = get_user()
        uid = user['id'] if user else 0
        guest_nick = request.form.get('guest_nick', '').strip() if not user else None
        guest_email = request.form.get('email', '').strip() if not user else None
        subject, msg = request.form['subject'], request.form['message']
        if not msg.strip():
            return render_template_string(SUPPORT_TMPL, error="Message required."), 400
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''INSERT INTO support_tickets (user_id, guest_nick, guest_email, subject, message) 
                       VALUES (?, ?, ?, ?, ?)''', (uid, guest_nick, guest_email, subject, msg))
        conn.commit()
        conn.close()
        return render_template_string(SUPPORT_TMPL, success=True)
    return render_template_string(SUPPORT_TMPL)

@app.route('/admin')
@check_access
@require_admin
def admin():
    db = db_conn()
    photos = db.execute('SELECT id, filename, original_name, title, privacy, views, downloads, likes FROM photos ORDER BY upload_date DESC').fetchall()
    users = db.execute('SELECT id, nickname, role, status, pfp_filename, comment_banned_until FROM users WHERE status="active" ORDER BY created_at DESC').fetchall()
    # ✅ NEW: Get banned users for management panel
    banned = db.execute('''SELECT id, nickname, ban_until, 
                          (SELECT reason FROM ip_bans WHERE ip_address = users.ip_address LIMIT 1) as ban_reason
                          FROM users WHERE status="banned" ORDER BY ban_until DESC''').fetchall()
    tickets = db.execute('''SELECT t.id, t.subject, t.status, t.admin_reply, t.message, t.guest_nick, t.guest_email, u.nickname as nick 
                           FROM support_tickets t LEFT JOIN users u ON t.user_id=u.id ORDER BY t.created_at DESC''').fetchall()
    offline = is_site_offline()
    maint_msg = db.execute('SELECT value FROM settings WHERE key="maintenance_msg"').fetchone()[0]
    db.close()
    return render_template_string(ADMIN_TMPL, photos=[dict(p) for p in photos], 
                                  users=[dict(u) for u in users],
                                  banned_users=[dict(b) for b in banned],  # ✅ Pass banned users to template
                                  tickets=[dict(t) for t in tickets], 
                                  offline=offline, maint_msg=maint_msg)

@app.route('/admin/photo/<int:pid>/delete', methods=['POST'])
@require_admin
def admin_del_photo(pid):
    conn = sqlite3.connect(DB_PATH)
    try: 
        fn = conn.execute('SELECT filename FROM photos WHERE id=?', (pid,)).fetchone()[0]
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], fn))
    except: pass
    for t in ['comments','user_likes','user_states','photo_access']: 
        conn.execute(f'DELETE FROM {t} WHERE photo_id=?', (pid,))
    conn.execute('DELETE FROM photos WHERE id=?', (pid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/ban', methods=['POST'])
@require_admin
def admin_ban(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET status="banned", ban_until=NULL WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/unban', methods=['POST'])  # ✅ NEW: Unban endpoint
@require_admin
def admin_unban(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET status="active", ban_until=NULL WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/timeout', methods=['POST'])
@require_admin
def admin_timeout(uid):
    until = (datetime.now() + timedelta(hours=24)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET status="banned", ban_until=? WHERE id=?', (until, uid))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/comment-ban', methods=['POST'])
@require_admin
def admin_comment_ban(uid):
    until = (datetime.now() + timedelta(days=7)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET comment_banned_until=? WHERE id=?', (until, uid))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/user/<int:uid>/promote', methods=['POST'])
@require_admin
def admin_promote(uid):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET role="admin" WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/ticket/<int:tid>/reply', methods=['POST'])
@require_admin
def admin_reply(tid):
    reply = request.form['reply']
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE support_tickets SET admin_reply=?, status="resolved" WHERE id=?', (reply, tid))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/toggle-offline', methods=['POST'])
@require_admin
def admin_toggle():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE settings SET value = ? WHERE key="site_offline"', ('1' if not is_site_offline() else '0'))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.route('/admin/set-maintenance', methods=['POST'])
@require_admin
def admin_set_maint():
    msg = request.form.get('msg', 'Site under maintenance')[:200]
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE settings SET value = ? WHERE key="maintenance_msg"', (msg,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin'))

@app.errorhandler(400)
def bad_request(e): return jsonify({'error': 'Bad request'}), 400
@app.errorhandler(404)
def not_found(e): return jsonify({'error': 'Not found'}), 404
@app.errorhandler(413)
def too_large(e): return jsonify({'error': 'File too large (max 32MB)'}), 413
@app.errorhandler(429)
def rate_limit(e): return jsonify({'error': 'Too many requests'}), 429
@app.errorhandler(500)
def server_error(e): return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
