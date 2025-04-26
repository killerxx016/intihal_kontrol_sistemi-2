import os
from difflib import SequenceMatcher
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
from functools import wraps

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB limit
app.config['DATABASE'] = 'app.db'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database Helper Functions
def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        db.execute(''' 
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute(''' 
            CREATE TABLE IF NOT EXISTS comparisons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text1 TEXT,
                text2 TEXT,
                score REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        db.commit()

# Initialize Database
init_db()

# Context Processor
@app.context_processor
def inject_current_year():
    return {'current_year': datetime.now().year}

# Helper Functions
def calculate_similarity(text1, text2):
    return round(SequenceMatcher(None, text1, text2).ratio() * 100, 2)

def extract_text_from_file(file):
    if not file or file.filename == '':
        return ""
    
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    try:
        if filename.endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        elif filename.endswith('.docx'):
            from docx import Document
            doc = Document(file_path)
            return '\n'.join([para.text for para in doc.paragraphs if para.text])
    except Exception as e:
        app.logger.error(f"Error extracting text: {e}")
        return ""
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Lütfen giriş yapınız', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session or session['role'] != role:
                flash('Bu sayfaya erişim izniniz yok', 'error')
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Lütfen kullanıcı adı ve şifre giriniz', 'error')
            return redirect(url_for('login'))
            
        db = get_db()
        user = db.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for(f"{user['role']}_dashboard"))
        else:
            flash('Geçersiz kullanıcı adı veya şifre', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        
        if not all([username, password, role]):
            flash('Lütfen tüm alanları doldurunuz', 'error')
            return redirect(url_for('register'))
            
        db = get_db()
        try:
            db.execute(
                'INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                (username, generate_password_hash(password), role)
            )
            db.commit()
            flash('Kayıt başarılı! Giriş yapabilirsiniz', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Bu kullanıcı adı zaten alınmış', 'error')
    
    return render_template('register.html')

@app.route('/compare', methods=['GET', 'POST'])
@login_required
def compare():
    score = None
    text1 = request.form.get('text1', '')
    text2 = request.form.get('text2', '')
    
    if request.method == 'POST':
        file1 = request.files.get('file1')
        file2 = request.files.get('file2')

        if file1 and file1.filename:
            text1 = extract_text_from_file(file1)
        if file2 and file2.filename:
            text2 = extract_text_from_file(file2)
        
        if text1 and text2:
            score = calculate_similarity(text1, text2)
            
            # Save comparison to database
            db = get_db()
            db.execute(
                'INSERT INTO comparisons (user_id, text1, text2, score) VALUES (?, ?, ?, ?)',
                (session['user_id'], text1, text2, score)
            )
            db.commit()
        else:
            flash('Lütfen iki metin veya dosya giriniz', 'error')
    
    return render_template('compare.html', score=score, text1=text1, text2=text2)

@app.route('/history')
@login_required
def history():
    db = get_db()
    
    if session['role'] == 'teacher':
        comparisons = db.execute(''' 
            SELECT c.*, u.username as user 
            FROM comparisons c
            JOIN users u ON c.user_id = u.id
            ORDER BY c.created_at DESC
            LIMIT 50
        ''').fetchall()
    else:
        comparisons = db.execute(''' 
            SELECT * FROM comparisons 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT 50
        ''', (session['user_id'],)).fetchall()
    
    return render_template('history.html', history=comparisons)

@app.route('/clear_history', methods=['POST'])
@login_required
@role_required('teacher')
def clear_history():
    db = get_db()
    db.execute('DELETE FROM comparisons')
    db.commit()
    flash('Geçmiş başarıyla temizlendi', 'success')
    return redirect(url_for('history'))

@app.route('/student_dashboard')
@login_required
@role_required('student')
def student_dashboard():
    db = get_db()

    recent_comparisons = db.execute(
        'SELECT * FROM comparisons WHERE user_id = ? ORDER BY created_at DESC LIMIT 5',
        (session['user_id'],)
    ).fetchall()

    # تحويل created_at من نص إلى datetime إذا كان من نوع string
    for comparison in recent_comparisons:
        created_at = comparison['created_at']
        if isinstance(created_at, str):
            comparison['created_at'] = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')

    stats = {
        'total_comparisons': db.execute(
            'SELECT COUNT(*) FROM comparisons WHERE user_id = ?',
            (session['user_id'],)
        ).fetchone()[0],
        'recent_comparisons': recent_comparisons
    }

    return render_template('student_dashboard.html', **stats)

@app.route('/student_dashboard')
@login_required
@role_required('student')
def student_dashboard():
    db = get_db()

    # استرجاع البيانات من قاعدة البيانات
    recent_comparisons = db.execute(
        'SELECT * FROM comparisons WHERE user_id = ? ORDER BY created_at DESC LIMIT 5',
        (session['user_id'],)
    ).fetchall()

    # تحويل created_at من نص إلى datetime إذا كان من نوع string
    for comparison in recent_comparisons:
        created_at = comparison['created_at']
        if isinstance(created_at, str):
            comparison['created_at'] = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')

    stats = {
        'total_comparisons': db.execute(
            'SELECT COUNT(*) FROM comparisons WHERE user_id = ?',
            (session['user_id'],)
        ).fetchone()[0],
        'recent_comparisons': recent_comparisons
    }

    return render_template('student_dashboard.html', **stats)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
