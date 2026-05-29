from flask import Flask, render_template, request, session, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
import os
import random
from datetime import datetime
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'campus_anonymous_chat_secret_key_2026'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['DATABASE'] = 'chat_database.db'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# semua function, route, dan socket handler di sini


# Allowed file extensions
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt'}

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# In-memory storage untuk online users
online_users = {}  # {username: {'rooms': [], 'socket_id': ''}}

# Database setup
def init_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    
    # Tabel messages
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT NOT NULL,
            username TEXT NOT NULL,
            message TEXT,
            file_path TEXT,
            timestamp TEXT NOT NULL,
            is_system INTEGER DEFAULT 0,
            is_private INTEGER DEFAULT 0,
            recipient TEXT
        )
    ''')
    
    # Tabel rooms
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Generate username anonymous random
def generate_anonymous_username():
    adjectives = ['Shy', 'Quiet', 'Mystery', 'Hidden', 'Silent', 'Calm', 'Smart', 'Cool', 'Wise', 'Bold']
    nouns = ['Student', 'Viewer', 'Listener', 'Reader', 'Builder', 'Thinker', 'Dreamer', 'Explorer', 'Wanderer']
    number = random.randint(100, 9999)
    return f"{random.choice(adjectives)}{random.choice(nouns)}{number}"

@app.route('/')
def index():
    if 'username' not in session:
        session['username'] = generate_anonymous_username()
    
    conn = get_db()
    rooms = conn.execute('SELECT name FROM rooms ORDER BY created_at DESC').fetchall()
    conn.close()
    
    return render_template('index.html', 
                          username=session['username'], 
                          rooms=[r['name'] for r in rooms])

@app.route('/chat/<room_name>')
def chat_room(room_name):
    if 'username' not in session:
        session['username'] = generate_anonymous_username()
    return render_template('chat.html', 
                          username=session['username'], 
                          room=room_name)

@app.route('/private/<recipient>')
def private_chat(recipient):
    if 'username' not in session:
        session['username'] = generate_anonymous_username()
    return render_template('private_chat.html',
                          username=session['username'],
                          recipient=recipient)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/online-users')
def get_online_users():
    users = list(online_users.keys())
    return jsonify({'users': users})

# Create room if not exists
def create_room_if_not_exists(room_name):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM rooms WHERE name = ?', (room_name,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO rooms (name, created_at) VALUES (?, ?)',
                      (room_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    conn.close()

@socketio.on('join')
def on_join(data):
    username = session['username']
    room = data['room']
    
    # Track online user
    if username not in online_users:
        online_users[username] = {'rooms': [], 'socket_id': request.sid}
    online_users[username]['rooms'].append(room)
    online_users[username]['socket_id'] = request.sid
    
    join_room(room)
    create_room_if_not_exists(room)
    
    # Get chat history from database
    conn = get_db()
    messages = conn.execute(
        'SELECT * FROM messages WHERE room = ? AND is_system = 0 ORDER BY timestamp DESC LIMIT 50',
        (room,)
    ).fetchall()
    conn.close()
    
    # Kirim history ke user yang join
    emit('chat_history', [{'username': m['username'], 'message': m['message'], 
                          'file_path': m['file_path'], 'timestamp': m['timestamp']}
                         for m in reversed(messages)])
    
    # Broadcast system message
    system_message = {
        'username': 'System',
        'message': f'{username} telah bergabung ke ruangan',
        'timestamp': datetime.now().strftime('%H:%M'),
        'is_system': True
    }
    emit('message', system_message, room=room)
    
    # Broadcast updated online users list
    broadcast_online_users()

@socketio.on('send_message')
def handle_message(data):
    username = session['username']
    room = data['room']
    message = data.get('message', '')
    file_path = data.get('file_path', '')
    is_private = data.get('is_private', False)
    recipient = data.get('recipient', '')
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    display_timestamp = datetime.now().strftime('%H:%M')
    
    # Simpan ke database
    conn = get_db()
    conn.execute(
        'INSERT INTO messages (room, username, message, file_path, timestamp, is_system, is_private, recipient) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (room, username, message, file_path, timestamp, 0, 1 if is_private else 0, recipient)
    )
    conn.commit()
    conn.close()
    
    message_data = {
        'username': username,
        'message': message,
        'file_path': file_path,
        'timestamp': display_timestamp,
        'is_system': False,
        'is_private': is_private,
        'recipient': recipient
    }
    
    if is_private and recipient:
        # Kirim private message ke recipient
        recipient_user = online_users.get(recipient)
        if recipient_user:
            emit('message', message_data, room=recipient_user['socket_id'])
        # juga kirim ke sender
        emit('message', message_data)
    else:
        # Broadcast ke semua di room yang sama
        emit('message', message_data, room=room)

@socketio.on('send_private_message')
def handle_private_message(data):
    username = session['username']
    recipient = data['recipient']
    message = data['message']
    file_path = data.get('file_path', '')
    
    # Private message menggunakan room khusus: private_sender_recipient
    room = f"private_{username}_{recipient}"
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    display_timestamp = datetime.now().strftime('%H:%M')
    
    # Simpan ke database
    conn = get_db()
    conn.execute(
        'INSERT INTO messages (room, username, message, file_path, timestamp, is_system, is_private, recipient) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (room, username, message, file_path, timestamp, 0, 1, recipient)
    )
    conn.commit()
    conn.close()
    
    message_data = {
        'username': username,
        'message': message,
        'file_path': file_path,
        'timestamp': display_timestamp,
        'is_system': False,
        'is_private': True,
        'recipient': recipient
    }
    
    # Kirim ke recipient
    recipient_user = online_users.get(recipient)
    if recipient_user:
        emit('message', message_data, room=recipient_user['socket_id'])
    
    # Kirim kembali ke sender
    emit('message', message_data)

@socketio.on('leave')
def on_leave(data):
    username = session['username']
    room = data['room']
    
    if username in online_users:
        if room in online_users[username]['rooms']:
            online_users[username]['rooms'].remove(room)
        if not online_users[username]['rooms']:
            del online_users[username]
    
    leave_room(room)
    
    system_message = {
        'username': 'System',
        'message': f'{username} telah keluar dari ruangan',
        'timestamp': datetime.now().strftime('%H:%M'),
        'is_system': True
    }
    emit('message', system_message, room=room)
    
    broadcast_online_users()

@socketio.on('disconnect')
def on_disconnect():
    # Find and remove user by socket_id
    username_to_remove = None
    for username, data in online_users.items():
        if data['socket_id'] == request.sid:
            username_to_remove = username
            break
    
    if username_to_remove:
        username = username_to_remove
        for room in online_users[username]['rooms']:
            system_message = {
                'username': 'System',
                'message': f'{username} telah keluar dari ruangan',
                'timestamp': datetime.now().strftime('%H:%M'),
                'is_system': True
            }
            emit('message', system_message, room=room)
        del online_users[username]
        broadcast_online_users()

def broadcast_online_users():
    online_list = list(online_users.keys())
    socketio.emit('online_users_update', {'users': online_list})

@socketio.on('upload_file')
def handle_file_upload(data):
    file = data.get('file')
    username = session['username']
    room = data['room']
    
    if file and file.filename:
        if allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Add unique identifier to prevent overwriting
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            
            emit('file_uploaded', {
                'file_path': f'/uploads/{unique_filename}',
                'filename': filename,
                'room': room
            })
        else:
            emit('file_error', {'message': 'File type tidak diizinkan!'})
    else:
        emit('file_error', {'message': 'Tidak ada file yang dipilih!'})

if __name__ == '__main__':
    # Buat folder uploads jika belum ada
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Initialize database
    init_db()
    
    print("🚀 Starting Anonymous Chat Campus...")
    print("📱 Access at: http://localhost:5000")
    print("📂 Upload folder: uploads/")
    print("💾 Database: chat_database.db")
    
    socketio.run(app, debug=True, host='0.0.0.0', port=5001)