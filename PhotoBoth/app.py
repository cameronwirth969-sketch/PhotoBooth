#!/usr/bin/env python3
"""
PhotoBoth - Modern Photo Sharing Platform
Deploy on Render.com - Single File Application
"""

import os
import sqlite3
import hashlib
import secrets
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, redirect, url_for, session, send_file, render_template_string, jsonify, abort
from werkzeug.utils import secure_filename
import mimetypes

# ============== CONFIGURATION ==============
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png'}
app.config['ADMIN_USERNAME'] = 'Admin'
# 🔐 SECURE PASSWORD: Change this or set via RENDER_ENV!
app.config['ADMIN_PASSWORD_HASH'] = os.environ.get('ADMIN_PASSWORD', 
    hashlib.sha256('PhotoBoth_Secure_2026!@#'.encode()).hexdigest())

DB_PATH = os.environ.get('DATABASE_URL', 'photoboth.db').replace('sqlite:///', '')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ============== DATABASE SETUP ==============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nickname TEXT UNIQUE NOT NULL,
        ip_address TEXT,
        is_banned INTEGER DEFAULT 0,
        ban_until TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Photos table
    c.execute('''CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        uploaded_by TEXT DEFAULT 'Admin',
        upload_date TEXT DEFAULT CURRENT_TIMESTAMP,
        views INTEGER DEFAULT 0,
        downloads INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
    )''')
    
    # Comments table
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_nickname TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (photo_id) REFERENCES photos (id)
    )''')
    
    # Likes tracking (prevent duplicate likes)
    c.execute('''CREATE TABLE IF NOT EXISTS user_likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_nickname TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(photo_id, user_nickname)
    )''')
    
    # User states tracking
    c.execute('''CREATE TABLE IF NOT EXISTS user_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        user_nickname TEXT NOT NULL,
        viewed INTEGER DEFAULT 0,
        downloaded INTEGER DEFAULT 0,
        last_viewed TEXT,
        UNIQUE(photo_id, user_nickname)
    )''')
    
    # IP bans
    c.execute('''CREATE TABLE IF NOT EXISTS ip_bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address TEXT UNIQUE NOT NULL,
        reason TEXT,
        banned_until TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Site settings
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("site_offline", "0")')
    
    conn.commit()
    conn.close()

init_db()

# ============== HELPER FUNCTIONS ==============
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr)

def is_ip_banned(ip):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT banned_until FROM ip_bans WHERE ip_address = ? AND (ban_until IS NULL OR ban_until > ?)', 
              (ip, datetime.now().isoformat()))
    result = c.fetchone()
    conn.close()
    return result is not None

def is_user_banned(nickname):
    if not nickname:
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT is_banned, ban_until FROM users WHERE nickname = ?', (nickname,))
    result = c.fetchone()
    conn.close()
    if result and result[0]:
        if result[1] and datetime.fromisoformat(result[1]) < datetime.now():
            # Unban if timeout expired
            conn = sqlite3.connect(DB_PATH)
            conn.execute('UPDATE users SET is_banned = 0, ban_until = NULL WHERE nickname = ?', (nickname,))
            conn.commit()
            conn.close()
            return False
        return True
    return False

def is_site_offline():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key = "site_offline"')
    result = c.fetchone()
    conn.close()
    return result and result[0] == '1'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============== DECORATORS ==============
def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('admin_logged_in'):
            return f(*args, **kwargs)
        return redirect(url_for('admin_login'))
    return decorated

def require_user(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = get_user_ip()
        if is_ip_banned(ip):
            return render_template_string(OFFLINE_TEMPLATE, message="Your IP has been banned."), 403
        if is_site_offline() and not session.get('admin_logged_in'):
            return render_template_string(OFFLINE_TEMPLATE), 503
        return f(*args, **kwargs)
    return decorated

# ============== HTML TEMPLATES (Embedded) ==============
BASE_CSS = '''
<style>
:root {
  --primary: #6366f1;
  --primary-dark: #4f46e5;
  --secondary: #ec4899;
  --bg-dark: #0f172a;
  --bg-card: #1e293b;
  --text: #f1f5f9;
  --text-muted: #94a3b8;
  --success: #22c55e;
  --danger: #ef4444;
  --warning: #f59e0b;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Segoe UI', system-ui, sans-serif;
  background: linear-gradient(135deg, var(--bg-dark) 0%, #1e1b4b 100%);
  color: var(--text);
  min-height: 100vh;
  line-height: 1.6;
}
.container { max-width: 1200px; margin: 0 auto; padding: 1rem; }
.header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1rem 0; border-bottom: 1px solid rgba(255,255,255,0.1);
  margin-bottom: 2rem;
}
.logo {
  font-size: 1.8rem; font-weight: 800;
  background: linear-gradient(90deg, var(--primary), var(--secondary));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  animation: pulse 3s infinite;
}
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.8; } }
.btn {
  padding: 0.6rem 1.4rem; border: none; border-radius: 8px;
  font-weight: 600; cursor: pointer; transition: all 0.2s;
  display: inline-flex; align-items: center; gap: 0.5rem;
}
.btn-primary { background: var(--primary); color: white; }
.btn-primary:hover { background: var(--primary-dark); transform: translateY(-2px); }
.btn-danger { background: var(--danger); color: white; }
.btn-sm { padding: 0.4rem 0.8rem; font-size: 0.85rem; }
.card {
  background: var(--bg-card); border-radius: 16px; padding: 1.5rem;
  margin-bottom: 1.5rem; box-shadow: 0 10px 25px rgba(0,0,0,0.3);
  animation: slideUp 0.4s ease;
}
@keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.photo-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1.5rem; margin-top: 1rem;
}
.photo-card {
  background: var(--bg-card); border-radius: 16px; overflow: hidden;
  transition: transform 0.3s, box-shadow 0.3s;
}
.photo-card:hover { transform: translateY(-5px); box-shadow: 0 20px 40px rgba(0,0,0,0.4); }
.photo-img {
  width: 100%; height: 220px; object-fit: cover;
  background: linear-gradient(45deg, #334155, #475569);
  display: flex; align-items: center; justify-content: center;
  color: var(--text-muted); font-size: 3rem;
}
.photo-info { padding: 1rem; }
.photo-stats {
  display: flex; gap: 1rem; margin: 0.8rem 0; color: var(--text-muted); font-size: 0.9rem;
}
.photo-stats span { display: flex; align-items: center; gap: 0.3rem; }
.photo-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.input-group { margin-bottom: 1rem; }
.input-group label { display: block; margin-bottom: 0.4rem; color: var(--text-muted); }
.input-group input, .input-group textarea {
  width: 100%; padding: 0.8rem; border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.05);
  color: var(--text); font-size: 1rem;
}
.input-group input:focus { outline: none; border-color: var(--primary); }
.badge {
  display: inline-block; padding: 0.25rem 0.6rem; border-radius: 20px;
  font-size: 0.75rem; font-weight: 600; margin-right: 0.3rem;
}
.badge-success { background: rgba(34,197,94,0.2); color: var(--success); }
.badge-warning { background: rgba(245,158,11,0.2); color: var(--warning); }
.badge-danger { background: rgba(239,68,68,0.2); color: var(--danger); }
.comments-section { margin-top: 1rem; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 1rem; }
.comment { padding: 0.6rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
.comment-header { display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-muted); }
.modal {
  display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  background: rgba(0,0,0,0.8); z-index: 1000; align-items: center; justify-content: center;
}
.modal.active { display: flex; animation: fadeIn 0.3s; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.modal-content {
  background: var(--bg-card); border-radius: 16px; padding: 2rem;
  max-width: 500px; width: 90%; max-height: 90vh; overflow-y: auto;
}
.toast {
  position: fixed; bottom: 2rem; right: 2rem; padding: 1rem 1.5rem;
  background: var(--bg-card); border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3);
  display: flex; align-items: center; gap: 0.8rem; z-index: 2000;
  animation: slideIn 0.3s, fadeOut 0.3s 2.7s forwards;
}
@keyframes slideIn { from { transform: translateX(100px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes fadeOut { to { opacity: 0; transform: translateY(20px); } }
.admin-panel { display: grid; grid-template-columns: 220px 1fr; gap: 2rem; }
.admin-sidebar { background: var(--bg-card); border-radius: 16px; padding: 1.5rem; height: fit-content; }
.admin-sidebar a {
  display: block; padding: 0.6rem 1rem; border-radius: 8px; color: var(--text-muted);
  text-decoration: none; transition: all 0.2s; margin-bottom: 0.3rem;
}
.admin-sidebar a:hover, .admin-sidebar a.active { background: var(--primary); color: white; }
.table { width: 100%; border-collapse: collapse; }
.table th, .table td { padding: 0.8rem; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }
.table th { color: var(--text-muted); font-weight: 600; font-size: 0.9rem; }
@media (max-width: 768px) {
  .admin-panel { grid-template-columns: 1fr; }
  .photo-grid { grid-template-columns: 1fr; }
}
</style>
'''

BASE_JS = '''
<script>
// Toast notifications
function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `<span>${message}</span>`;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// Like functionality
async function likePhoto(photoId) {
  const res = await fetch(`/api/like/${photoId}`, { method: 'POST' });
  const data = await res.json();
  if (data.success) {
    document.getElementById(`likes-${photoId}`).textContent = data.likes;
    showToast('❤️ Liked!', 'success');
  } else {
    showToast(data.error || 'Already liked', 'warning');
  }
}

// Track view
async function trackView(photoId) {
  await fetch(`/api/view/${photoId}`, { method: 'POST' });
}

// Download tracking
function trackDownload(photoId, filename) {
  fetch(`/api/download/${photoId}`, { method: 'POST' });
  setTimeout(() => window.location.href = `/uploads/${filename}`, 100);
}

// Modal handling
function openModal(id) { document.getElementById(id).classList.add('active'); }
function closeModal(id) { document.getElementById(id).classList.remove('active'); }

// Form validation
document.querySelectorAll('form').forEach(form => {
  form.addEventListener('submit', async (e) => {
    const fileInput = form.querySelector('input[type="file"]');
    if (fileInput && fileInput.files[0]) {
      const ext = fileInput.files[0].name.split('.').pop().toLowerCase();
      if (ext !== 'png') {
        e.preventDefault();
        showToast('Only PNG files allowed!', 'error');
      }
    }
  });
});

// Auto-track views on photo load
document.querySelectorAll('.photo-card').forEach(card => {
  const photoId = card.dataset.photoId;
  if (photoId) trackView(photoId);
});
</script>
'''

OFFLINE_TEMPLATE = f'''<!DOCTYPE html>
<html><head><title>PhotoBoth - Offline</title>{BASE_CSS}</head>
<body><div class="container" style="text-align:center;padding-top:100px">
  <h1 style="font-size:3rem;margin-bottom:1rem">🔧 {{% if message %}}{{{{message}}}}{{% else %}}Site Offline{{% endif %}}</h1>
  <p style="color:var(--text-muted);margin-bottom:2rem">We'll be back soon! Check back later.</p>
  <a href="/" class="btn btn-primary">Refresh</a>
</div></body></html>'''

LOGIN_TEMPLATE = f'''<!DOCTYPE html>
<html><head><title>PhotoBoth - Login</title>{BASE_CSS}</head>
<body>
<div class="container" style="max-width:400px;margin:100px auto">
  <div class="card">
    <h2 style="text-align:center;margin-bottom:1.5rem">🔐 Admin Login</h2>
    <form method="POST" action="/adminui/login">
      <div class="input-group">
        <label>Username</label>
        <input type="text" name="username" required autocomplete="username">
      </div>
      <div class="input-group">
        <label>Password</label>
        <input type="password" name="password" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%">Sign In</button>
    </form>
    <p style="text-align:center;margin-top:1rem;color:var(--text-muted);font-size:0.9rem">
      <a href="/" style="color:var(--primary);text-decoration:none">← Back to Gallery</a>
    </p>
  </div>
</div>
{BASE_JS}</body></html>'''

MAIN_TEMPLATE = f'''<!DOCTYPE html>
<html><head><title>PhotoBoth</title><meta name="viewport" content="width=device-width,initial-scale=1">{BASE_CSS}</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo">📸 PhotoBoth</div>
    <div style="display:flex;gap:1rem;align-items:center">
      {{% if session.nickname %}}
        <span style="color:var(--text-muted)">👤 {{% session.nickname %}}</span>
        <a href="/logout" class="btn btn-sm btn-danger">Logout</a>
      {{% else %}}
        <button class="btn btn-primary" onclick="openModal('nicknameModal')">Set Nickname</button>
      {{% endif %}}
      <a href="/adminui" class="btn btn-sm" style="background:rgba(255,255,255,0.1)">Admin</a>
    </div>
  </div>

  <!-- Upload Section for Admin -->
  {{% if session.admin_logged_in %}}
  <div class="card">
    <h3>📤 Upload Photo (PNG Only)</h3>
    <form method="POST" action="/adminui/upload" enctype="multipart/form-data" style="display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem">
      <input type="file" name="file" accept=".png" required style="flex:1;min-width:200px">
      <button type="submit" class="btn btn-primary">Upload</button>
    </form>
  </div>
  {{% endif %}}

  <!-- Photo Grid -->
  <div class="photo-grid">
    {{% for photo in photos %}}
    <div class="photo-card" data-photo-id="{{% photo.id %}}">
      <div class="photo-img" style="background-image:url('/uploads/{{% photo.filename %}}');background-size:cover;background-position:center"></div>
      <div class="photo-info">
        <strong>{{% photo.original_name %}}</strong>
        <div class="photo-stats">
          <span>👁️ <span id="views-{{% photo.id %}}">{{% photo.views %}}</span></span>
          <span>⬇️ <span id="downloads-{{% photo.id %}}">{{% photo.downloads %}}</span></span>
          <span>❤️ <span id="likes-{{% photo.id %}}">{{% photo.likes %}}</span></span>
        </div>
        <div class="photo-actions">
          <button class="btn btn-sm btn-primary" onclick="likePhoto({{% photo.id %}})">Like</button>
          <button class="btn btn-sm" onclick="trackDownload({{% photo.id %}}, '{{% photo.filename %}}')">Download</button>
          <button class="btn btn-sm" onclick="openModal('commentModal-{{% photo.id %}}')">💬 Comment</button>
        </div>
        
        <!-- Comments -->
        <div class="comments-section">
          {{% for comment in comments.get(photo.id, []) %}}
          <div class="comment">
            <div class="comment-header">
              <strong>{{% comment.user_nickname %}}</strong>
              <span>{{% comment.created_at[:10] %}}</span>
            </div>
            <p style="font-size:0.95rem">{{% comment.content %}}</p>
            {{% if session.admin_logged_in %}}
            <form method="POST" action="/adminui/comment/delete/{{% comment.id %}}" style="display:inline">
              <button type="submit" class="btn btn-sm btn-danger" onclick="return confirm('Delete comment?')">🗑️</button>
            </form>
            {{% endif %}}
          </div>
          {{% endfor %}}
        </div>
      </div>
    </div>
    
    <!-- Comment Modal -->
    <div class="modal" id="commentModal-{{% photo.id %}}">
      <div class="modal-content">
        <h4>💬 Add Comment</h4>
        <form method="POST" action="/comment/{{% photo.id %}}" style="margin-top:1rem">
          <div class="input-group">
            <textarea name="content" rows="3" placeholder="Write a comment..." required></textarea>
          </div>
          <div style="display:flex;gap:0.5rem;justify-content:flex-end">
            <button type="button" class="btn btn-sm" onclick="closeModal('commentModal-{{% photo.id %}}')">Cancel</button>
            <button type="submit" class="btn btn-sm btn-primary">Post</button>
          </div>
        </form>
      </div>
    </div>
    {{% endfor %}}
  </div>
</div>

<!-- Nickname Modal -->
<div class="modal" id="nicknameModal">
  <div class="modal-content">
    <h3>👋 Choose Your Nickname</h3>
    <p style="color:var(--text-muted);margin:0.5rem 0 1rem">This will identify you for likes and comments</p>
    <form method="POST" action="/set-nickname">
      <div class="input-group">
        <input type="text" name="nickname" placeholder="Enter unique nickname" required pattern="[a-zA-Z0-9_]{{3,20}}" title="3-20 chars, letters/numbers/underscores">
      </div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end">
        <button type="submit" class="btn btn-primary">Save Nickname</button>
      </div>
    </form>
  </div>
</div>

{BASE_JS}
<script>
// Close modals on outside click
document.querySelectorAll('.modal').forEach(modal => {
  modal.addEventListener('click', (e) => {{
    if (e.target === modal) modal.classList.remove('active');
  }});
});
</script>
</body></html>'''

ADMIN_TEMPLATE = f'''<!DOCTYPE html>
<html><head><title>PhotoBoth Admin</title><meta name="viewport" content="width=device-width,initial-scale=1">{BASE_CSS}</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo">⚙️ Admin Panel</div>
    <a href="/" class="btn btn-sm">← Gallery</a>
  </div>
  
  <div class="admin-panel">
    <div class="admin-sidebar">
      <a href="#photos" class="active">📸 Photos</a>
      <a href="#users">👥 Users</a>
      <a href="#bans">🚫 Bans</a>
      <a href="#settings">⚙️ Settings</a>
      <a href="/adminui/logout" style="margin-top:2rem;color:var(--danger)">🚪 Logout</a>
    </div>
    
    <div>
      <!-- Photos Management -->
      <div id="photos" class="card">
        <h3>Manage Photos</h3>
        <table class="table">
          <thead><tr><th>Image</th><th>Name</th><th>Stats</th><th>Actions</th></tr></thead>
          <tbody>
            {{% for photo in photos %}}
            <tr>
              <td><img src="/uploads/{{% photo.filename %}}" style="width:50px;height:50px;object-fit:cover;border-radius:4px"></td>
              <td>{{% photo.original_name %}}<br><small style="color:var(--text-muted)">{{% photo.uploaded_by %}}</small></td>
              <td>👁️{{% photo.views %}} ⬇️{{% photo.downloads %}} ❤️{{% photo.likes %}}</td>
              <td>
                <form method="POST" action="/adminui/photo/delete/{{% photo.id %}}" style="display:inline" onsubmit="return confirm('Delete this photo?')">
                  <button type="submit" class="btn btn-sm btn-danger">🗑️</button>
                </form>
              </td>
            </tr>
            {{% endfor %}}
          </tbody>
        </table>
      </div>
      
      <!-- User Management -->
      <div id="users" class="card">
        <h3>Manage Users</h3>
        <table class="table">
          <thead><tr><th>Nickname</th><th>IP</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            {{% for user in users %}}
            <tr>
              <td>{{% user.nickname %}}</td>
              <td><small>{{% user.ip_address %}}</small></td>
              <td>
                {{% if user.is_banned %}}
                  <span class="badge badge-danger">Banned</span>
                {{% else %}}
                  <span class="badge badge-success">Active</span>
                {{% endif %}}
              </td>
              <td style="display:flex;gap:0.3rem;flex-wrap:wrap">
                <form method="POST" action="/adminui/user/ban/{{% user.nickname %}}" style="display:inline">
                  <button type="submit" class="btn btn-sm btn-danger" title="Ban">🔒</button>
                </form>
                <form method="POST" action="/adminui/user/timeout/{{% user.nickname %}}" style="display:inline">
                  <button type="submit" class="btn btn-sm btn-warning" title="24h Timeout">⏱️</button>
                </form>
                <form method="POST" action="/adminui/user/ipban/{{% user.ip_address %}}" style="display:inline" onsubmit="return confirm('Ban this IP?')">
                  <button type="submit" class="btn btn-sm" style="background:#7c3aed" title="IP Ban">🌐</button>
                </form>
              </td>
            </tr>
            {{% endfor %}}
          </tbody>
        </table>
      </div>
      
      <!-- Active Bans -->
      <div id="bans" class="card">
        <h3>Active Bans</h3>
        <table class="table">
          <thead><tr><th>Type</th><th>Target</th><th>Until</th><th>Actions</th></tr></thead>
          <tbody>
            {{% for ban in ip_bans %}}
            <tr>
              <td><span class="badge badge-danger">IP</span></td>
              <td>{{% ban.ip_address %}}</td>
              <td>{{% ban.banned_until or 'Permanent' %}}</td>
              <td>
                <form method="POST" action="/adminui/ban/remove/{{% ban.id %}}" style="display:inline">
                  <button type="submit" class="btn btn-sm" style="background:var(--success)">✅ Unban</button>
                </form>
              </td>
            </tr>
            {{% endfor %}}
          </tbody>
        </table>
      </div>
      
      <!-- Settings -->
      <div id="settings" class="card">
        <h3>Site Settings</h3>
        <div style="display:flex;align-items:center;gap:1rem;padding:1rem;background:rgba(255,255,255,0.05);border-radius:12px">
          <div style="flex:1">
            <strong>🔌 Site Status</strong>
            <p style="color:var(--text-muted);font-size:0.9rem">Put the website in offline mode</p>
          </div>
          <form method="POST" action="/adminui/toggle-offline">
            <button type="submit" class="btn {{% 'btn-danger' if site_offline else 'btn-success' }}" 
                    style="background:{{% 'var(--danger)' if site_offline else 'var(--success)' %}}">
              {{% 'Bring Online' if site_offline else 'Take Offline' %}}
            </button>
          </form>
        </div>
      </div>
    </div>
  </div>
</div>
{BASE_JS}</body></html>'''

# ============== ROUTES ==============
@app.route('/')
@require_user
def index():
    conn = get_db()
    photos = conn.execute('SELECT * FROM photos WHERE is_active = 1 ORDER BY upload_date DESC').fetchall()
    
    # Get comments for each photo
    comments = {}
    for photo in photos:
        comments[photo['id']] = conn.execute(
            'SELECT * FROM comments WHERE photo_id = ? ORDER BY created_at DESC LIMIT 5', 
            (photo['id'],)
        ).fetchall()
    
    conn.close()
    return render_template_string(MAIN_TEMPLATE, photos=photos, comments=comments, session=session)

@app.route('/set-nickname', methods=['POST'])
@require_user
def set_nickname():
    nickname = request.form.get('nickname', '').strip()
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', nickname):
        return jsonify({'error': 'Nickname must be 3-20 chars, letters/numbers/underscores'}), 400
    
    ip = get_user_ip()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('INSERT INTO users (nickname, ip_address) VALUES (?, ?)', (nickname, ip))
        conn.commit()
        session['nickname'] = nickname
        session['ip'] = ip
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Nickname already taken'}), 409
    conn.close()
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/view/<int:photo_id>', methods=['POST'])
@require_user
def track_view(photo_id):
    nickname = session.get('nickname')
    conn = sqlite3.connect(DB_PATH)
    
    # Update photo views
    conn.execute('UPDATE photos SET views = views + 1 WHERE id = ?', (photo_id,))
    
    # Track user state
    if nickname:
        conn.execute('''INSERT INTO user_states (photo_id, user_nickname, viewed, last_viewed) 
                       VALUES (?, ?, 1, ?) 
                       ON CONFLICT(photo_id, user_nickname) DO UPDATE SET viewed=1, last_viewed=?''',
                    (photo_id, nickname, datetime.now().isoformat(), datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/download/<int:photo_id>', methods=['POST'])
@require_user
def track_download(photo_id):
    nickname = session.get('nickname')
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE photos SET downloads = downloads + 1 WHERE id = ?', (photo_id,))
    
    if nickname:
        conn.execute('''INSERT INTO user_states (photo_id, user_nickname, downloaded) 
                       VALUES (?, ?, 1) 
                       ON CONFLICT(photo_id, user_nickname) DO UPDATE SET downloaded=1''',
                    (photo_id, nickname))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/uploads/<filename>')
@require_user
def uploaded_file(filename):
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(filename)))

@app.route('/api/like/<int:photo_id>', methods=['POST'])
@require_user
def like_photo(photo_id):
    nickname = session.get('nickname')
    if not nickname:
        return jsonify({'error': 'Set a nickname first'}), 401
    
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('INSERT INTO user_likes (photo_id, user_nickname) VALUES (?, ?)', (photo_id, nickname))
        conn.execute('UPDATE photos SET likes = likes + 1 WHERE id = ?', (photo_id,))
        conn.commit()
        likes = conn.execute('SELECT likes FROM photos WHERE id = ?', (photo_id,)).fetchone()[0]
        conn.close()
        return jsonify({'success': True, 'likes': likes})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Already liked'}), 400

@app.route('/comment/<int:photo_id>', methods=['POST'])
@require_user
def add_comment(photo_id):
    nickname = session.get('nickname')
    if not nickname:
        return jsonify({'error': 'Set a nickname first'}), 401
    
    content = request.form.get('content', '').strip()
    if len(content) < 2 or len(content) > 500:
        return jsonify({'error': 'Comment must be 2-500 characters'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT INTO comments (photo_id, user_nickname, content) VALUES (?, ?, ?)',
                 (photo_id, nickname, content))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

# ============== ADMIN ROUTES ==============
@app.route('/adminui')
def admin_login_page():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_panel'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/adminui/login', methods=['POST'])
def admin_login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    if username == app.config['ADMIN_USERNAME'] and hash_password(password) == app.config['ADMIN_PASSWORD_HASH']:
        session['admin_logged_in'] = True
        session['admin_user'] = username
        return redirect(url_for('admin_panel'))
    
    return render_template_string(LOGIN_TEMPLATE), 401

@app.route('/adminui/panel')
@require_admin
def admin_panel():
    conn = get_db()
    photos = conn.execute('SELECT * FROM photos ORDER BY upload_date DESC').fetchall()
    users = conn.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()
    ip_bans = conn.execute('SELECT * FROM ip_bans ORDER BY created_at DESC').fetchall()
    site_offline = is_site_offline()
    conn.close()
    return render_template_string(ADMIN_TEMPLATE, photos=photos, users=users, ip_bans=ip_bans, site_offline=site_offline)

@app.route('/adminui/upload', methods=['POST'])
@require_admin
def admin_upload():
    if 'file' not in request.files:
        return redirect(url_for('admin_panel'))
    
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': 'Only PNG files allowed'}), 400
    
    filename = secure_filename(f"{secrets.token_hex(8)}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT INTO photos (filename, original_name, uploaded_by) VALUES (?, ?, ?)',
                 (filename, file.filename, session.get('admin_user', 'Admin')))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_panel'))

@app.route('/adminui/photo/delete/<int:photo_id>', methods=['POST'])
@require_admin
def delete_photo(photo_id):
    conn = sqlite3.connect(DB_PATH)
    photo = conn.execute('SELECT filename FROM photos WHERE id = ?', (photo_id,)).fetchone()
    if photo:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], photo['filename']))
        except:
            pass
        conn.execute('DELETE FROM photos WHERE id = ?', (photo_id,))
        conn.execute('DELETE FROM comments WHERE photo_id = ?', (photo_id,))
        conn.execute('DELETE FROM user_likes WHERE photo_id = ?', (photo_id,))
        conn.execute('DELETE FROM user_states WHERE photo_id = ?', (photo_id,))
        conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/adminui/comment/delete/<int:comment_id>', methods=['POST'])
@require_admin
def delete_comment(comment_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/adminui/user/ban/<nickname>', methods=['POST'])
@require_admin
def ban_user(nickname):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET is_banned = 1 WHERE nickname = ?', (nickname,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/adminui/user/timeout/<nickname>', methods=['POST'])
@require_admin
def timeout_user(nickname):
    until = (datetime.now() + timedelta(hours=24)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE users SET is_banned = 1, ban_until = ? WHERE nickname = ?', (until, nickname))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/adminui/user/ipban/<ip_address>', methods=['POST'])
@require_admin
def ban_ip(ip_address):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT OR REPLACE INTO ip_bans (ip_address, reason) VALUES (?, ?)', 
                 (ip_address, 'Admin ban'))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/adminui/ban/remove/<int:ban_id>', methods=['POST'])
@require_admin
def remove_ban(ban_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM ip_bans WHERE id = ?', (ban_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/adminui/toggle-offline', methods=['POST'])
@require_admin
def toggle_offline():
    conn = sqlite3.connect(DB_PATH)
    current = is_site_offline()
    conn.execute('UPDATE settings SET value = ? WHERE key = "site_offline"', 
                 ('1' if not current else '0'))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/adminui/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_user', None)
    return redirect(url_for('index'))

# ============== ERROR HANDLERS ==============
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large (max 16MB)'}), 413

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Server error'}), 500

# ============== RENDER.COM DEPLOYMENT ==============
if __name__ == '__main__':
    # For local development
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)