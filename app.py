import os
from datetime import datetime, timezone, timedelta, date
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from markupsafe import Markup
import click
import random
import re # 自然順ソートのために正規表現ライブラリをインポート

# JSTタイムゾーンの定義 (UTC+9)
JST = timezone(timedelta(hours=+9), 'JST')

app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_default_secret_key_for_development')
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'boards.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_recycle": 280}

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "このページにアクセスするにはログインが必要です。"
login_manager.login_message_category = "error"

# --- Custom Jinja Filter ---
@app.template_filter('nl2br')
def nl2br(s):
    if s:
        return Markup(s.replace('\n', '<br>\n'))
    return ''

# --- Association Table for Session Members ---
session_members = db.Table('session_members',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('session_id', db.Integer, db.ForeignKey('practice_session.id'), primary_key=True)
)

# --- Models ---
class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    users = db.relationship('User', backref='team', lazy=True)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='member')
    generation = db.Column(db.String(20), nullable=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)
    transport_count = db.Column(db.Integer, nullable=False, default=0)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Board(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    serial_number = db.Column(db.String(100), unique=True, nullable=True)
    location = db.Column(db.String(100), nullable=False)
    user = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    histories = db.relationship('UpdateHistory', backref='board', lazy=True, cascade="all, delete-orphan")

class UpdateHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('board.id'), nullable=False)
    previous_location = db.Column(db.String(100))
    new_location = db.Column(db.String(100), nullable=False)
    updated_by = db.Column(db.String(50), nullable=False)
    updated_at = db.Column(db.String(50), nullable=False)

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(JST))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    author = db.relationship('User', backref=db.backref('announcements', lazy=True))

class Practice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="チーム練習")
    practice_date = db.Column(db.Date, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    target_team = db.relationship('Team')
    sessions = db.relationship('PracticeSession', backref='practice', lazy=True, cascade="all, delete-orphan")
    attendances = db.relationship('Attendance', backref='practice', lazy=True, cascade="all, delete-orphan")
    transports = db.relationship('Transport', backref='practice', lazy=True, cascade="all, delete-orphan")

class PracticeSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey('practice.id'), nullable=False)
    session_number = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    members = db.relationship('User', secondary=session_members, backref=db.backref('sessions_attending', lazy='dynamic'))

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey('practice.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User')
    status = db.Column(db.String(20), nullable=False, default='unanswered')
    reason = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

class Transport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey('practice.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    board_id = db.Column(db.Integer, db.ForeignKey('board.id'), nullable=False)
    direction = db.Column(db.String(10), nullable=False)
    user = db.relationship('User')
    board = db.relationship('Board')

# --- Decorators ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not hasattr(current_user, 'role') or current_user.role != 'admin':
            flash('このページにアクセスするには管理者権限が必要です。', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def member_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if hasattr(current_user, 'role') and current_user.role == 'guest':
            flash('ゲストユーザーはこの操作を実行できません。', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# --- Flask-Login Helper ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Database Initialization ---
with app.app_context():
    db.create_all()

# --- CLI Command for Admin Promotion ---
@app.cli.command("promote-admin")
@click.argument("username")
def promote_admin_command(username):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if user:
            user.role = 'admin'
            db.session.commit()
            print(f"ユーザー '{username}' は管理者に昇格しました。")
        else:
            print(f"ユーザー '{username}' が見つかりません。")

# --- Main Routes ---
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    unanswered_attendances = Attendance.query.filter_by(user_id=current_user.id, status='unanswered').join(Practice).order_by(Practice.practice_date).all()
    announcements = Announcement.query.order_by(Announcement.timestamp.desc()).all()
    return render_template('dashboard.html', unanswered_attendances=unanswered_attendances, announcements=announcements)


# --- User Auth & Profile Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for('dashboard'))
        flash('ユーザー名またはパスワードが正しくありません。', 'error')
    return render_template('auth/login.html')

@app.route('/guest-login')
def guest_login():
    guest_user = User.query.filter_by(role='guest').first()
    if not guest_user:
        guest_user = User(username='guest', role='guest')
        guest_user.set_password(os.urandom(16).hex())
        db.session.add(guest_user)
        db.session.commit()
    login_user(guest_user, remember=True)
    return redirect(url_for('dashboard'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not all([username, password]):
            flash('ユーザー名とパスワードの両方を入力してください。', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('そのユーザー名は既に使用されています。', 'error')
            return redirect(url_for('register'))
        
        # 最初のユーザーを自動で管理者に設定
        is_first_user = User.query.count() == 0
        role = 'admin' if is_first_user else 'member'
        
        new_user = User(username=username, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        if is_first_user:
            flash('最初のユーザーとして登録され、管理者権限が付与されました。ログイン後、プロフィールで詳細を設定してください。', 'success')
        else:
            flash('ユーザー登録が完了しました。ログインしてプロフィールを設定してください。', 'success')
        return redirect(url_for('login'))
    return render_template('auth/register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    teams = Team.query.order_by(Team.name).all()
    if request.method == 'POST':
        new_username = request.form.get('username')
        if new_username != current_user.username and User.query.filter_by(username=new_username).first():
            flash('そのユーザー名は既に使用されています。', 'error')
            return redirect(url_for('profile'))
        current_user.username = new_username
        current_user.generation = request.form.get('generation')
        current_user.team_id = request.form.get('team_id')
        password = request.form.get('password')
        if password:
            current_user.set_password(password)
        db.session.commit()
        flash('プロフィールが更新されました。', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html', teams=teams)

# --- Board Management Routes ---
@app.route('/boards')
@login_required
def board_index():
    sort_by = request.args.get('sort_by', 'id')
    order = request.args.get('order', 'asc')
    
    # まず、すべてのボードをデータベースから取得
    all_boards_query = Board.query.all()

    if sort_by == 'name':
        # Python側で自然順ソートを実行
        def natural_sort_key(board):
            parts = re.split('([0-9]+)', board.name)
            parts[1::2] = [int(p) for p in parts[1::2] if p]
            return parts
        all_boards = sorted(all_boards_query, key=natural_sort_key, reverse=(order == 'desc'))
    else:
        # デフォルトはID順
        all_boards = sorted(all_boards_query, key=lambda b: b.id, reverse=(order == 'desc'))

    location_counts = {}
    for board in all_boards:
        location_counts[board.location] = location_counts.get(board.location, 0) + 1
    return render_template('boards/index.html', boards=all_boards, location_counts=location_counts)

@app.route('/boards/add', methods=['GET', 'POST'])
@login_required
@member_required
def add_board():
    if request.method == 'POST':
        name = request.form.get('name')
        serial_number = request.form.get('serial_number') or None
        notes = request.form.get('notes')
        location_select = request.form.get('location_select')
        user = current_user.username
        if not all([name, location_select]):
            flash('必須項目が入力されていません。', 'error')
            return redirect(url_for('add_board'))
        if Board.query.filter_by(name=name).first():
            flash(f'ボード名「{name}」は既に使用されています。', 'error')
            return redirect(url_for('add_board'))
        if serial_number and Board.query.filter_by(serial_number=serial_number).first():
            flash(f'シリアル番号「{serial_number}」は既に使用されています。', 'error')
            return redirect(url_for('add_board'))
        location = request.form.get('location_other') if location_select == 'その他' else location_select
        updated_at = datetime.now(JST).strftime('%Y/%m/%d %H:%M')
        new_board = Board(name=name, serial_number=serial_number, location=location, user=user, notes=notes, updated_at=updated_at)
        db.session.add(new_board)
        db.session.commit()
        flash(f'ボード「{name}」が正常に追加されました。', 'success')
        return redirect(url_for('board_index'))
    return render_template('boards/add.html')

@app.route('/boards/update/<int:board_id>', methods=['GET', 'POST'])
@login_required
@member_required
def update_board(board_id):
    board_to_update = Board.query.get_or_404(board_id)
    if request.method == 'POST':
        previous_location = board_to_update.location
        previous_user = board_to_update.user
        new_name = request.form.get('name')
        new_serial_number = request.form.get('serial_number') or None
        notes = request.form.get('notes')
        location_select = request.form.get('location_select')
        new_user = current_user.username
        if not all([new_name, location_select]):
            flash('必須項目が入力されていません。', 'error')
            return redirect(url_for('update_board', board_id=board_id))
        if Board.query.filter(Board.name == new_name, Board.id != board_id).first():
            flash(f'ボード名「{new_name}」は既に使用されています。', 'error')
            return redirect(url_for('update_board', board_id=board_id))
        if new_serial_number and Board.query.filter(Board.serial_number == new_serial_number, Board.id != board_id).first():
            flash(f'シリアル番号「{new_serial_number}」は既に使用されています。', 'error')
            return redirect(url_for('update_board', board_id=board_id))
        new_location = request.form.get('location_other') if location_select == 'その他' else location_select
        current_time_jst = datetime.now(JST).strftime('%Y/%m/%d %H:%M')
        if previous_location != new_location or previous_user != new_user:
            history_entry = UpdateHistory(board_id=board_id, previous_location=previous_location, new_location=new_location, updated_by=new_user, updated_at=current_time_jst)
            db.session.add(history_entry)
        board_to_update.name = new_name
        board_to_update.serial_number = new_serial_number
        board_to_update.notes = notes
        board_to_update.location = new_location
        board_to_update.user = new_user
        board_to_update.updated_at = current_time_jst
        db.session.commit()
        flash(f'ボード「{board_to_update.name}」が正常に更新されました。', 'success')
        return redirect(url_for('board_index'))
    return render_template('boards/update.html', board=board_to_update)

@app.route('/boards/delete/<int:board_id>', methods=['POST'])
@login_required
@member_required
def delete_board(board_id):
    board_to_delete = Board.query.get_or_404(board_id)
    db.session.delete(board_to_delete)
    db.session.commit()
    flash(f'ボード「{board_to_delete.name}」を削除しました。', 'success')
    return redirect(url_for('board_index'))

@app.route('/boards/history/<int:board_id>')
@login_required
def history(board_id):
    board = Board.query.get_or_404(board_id)
    histories = UpdateHistory.query.filter_by(board_id=board.id).order_by(UpdateHistory.id.desc()).all()
    return render_template('boards/history.html', board=board, histories=histories)

@app.route('/boards/bulk_update', methods=['POST'])
@login_required
@member_required
def bulk_update():
    board_ids = request.form.getlist('board_ids')
    if not board_ids:
        flash('更新するボードが選択されていません。', 'error')
        return redirect(url_for('board_index'))
    updater = current_user.username
    location_select = request.form.get('location_select')
    new_location = request.form.get('location_other') if location_select == 'その他' else location_select
    current_time_jst = datetime.now(JST).strftime('%Y/%m/%d %H:%M')
    updated_count = 0
    for board_id in board_ids:
        board = Board.query.get(board_id)
        if board:
            previous_location = board.location
            previous_user = board.user
            if previous_location != new_location or previous_user != updater:
                history_entry = UpdateHistory(board_id=board.id, previous_location=previous_location, new_location=new_location, updated_by=updater, updated_at=current_time_jst)
                db.session.add(history_entry)
            board.location = new_location
            board.user = updater
            board.updated_at = current_time_jst
            updated_count += 1
    if updated_count > 0:
        db.session.commit()
        flash(f'{updated_count}件のボード情報を一括更新しました。', 'success')
    return redirect(url_for('board_index'))
    
# --- Practice Management Routes ---
@app.route('/practices')
@login_required
def practice_index():
    practices = Practice.query.order_by(Practice.practice_date.desc()).all()
    return render_template('practice/index.html', practices=practices)

@app.route('/practices/new', methods=['GET', 'POST'])
@login_required
@admin_required
def create_practice():
    teams = Team.query.order_by(Team.name).all()
    generations = db.session.query(User.generation).distinct().order_by(User.generation).all()
    generations = [gen[0] for gen in generations if gen[0]]
    if request.method == 'POST':
        title = request.form.get('title')
        practice_date_str = request.form.get('practice_date')
        location = request.form.get('location')
        team_id = request.form.get('team_id')
        target_generations = request.form.getlist('generations')
        if not all([title, practice_date_str, location, team_id, target_generations]):
            flash('すべての項目を入力してください。', 'error')
            return redirect(url_for('create_practice'))
        practice_date = datetime.strptime(practice_date_str, '%Y-%m-%d').date()
        new_practice = Practice(title=title, practice_date=practice_date, location=location, team_id=team_id)
        db.session.add(new_practice)
        target_users = User.query.filter(User.team_id == team_id, User.generation.in_(target_generations)).all()
        if not target_users:
            flash('対象となるユーザーが見つかりませんでした。', 'warning')
            db.session.rollback()
            return redirect(url_for('create_practice'))
        for user in target_users:
            attendance = Attendance(practice=new_practice, user_id=user.id, status='unanswered')
            db.session.add(attendance)
        db.session.commit()
        flash(f'新しい練習「{title}」を作成し、{len(target_users)}人に出欠確認を送信しました。', 'success')
        return redirect(url_for('practice_index'))
    return render_template('practice/create.html', teams=teams, generations=generations)

@app.route('/practices/<int:practice_id>')
@login_required
def practice_detail(practice_id):
    practice = Practice.query.get_or_404(practice_id)
    user_attendance = Attendance.query.filter_by(practice_id=practice.id, user_id=current_user.id).first()
    all_attendances = Attendance.query.filter_by(practice_id=practice.id).join(User).order_by(User.generation, User.username).all()
    boards_at_location = Board.query.filter_by(location=practice.location).count()
    assignable_attendees = [att.user for att in all_attendances if att.status in ['present', 'late_leave']]
    assigned_user_ids = [member.id for session in practice.sessions for member in session.members]
    unassigned_attendees = [user for user in assignable_attendees if user.id not in assigned_user_ids]
    max_session_members = 0
    for session in practice.sessions:
        if len(session.members) > max_session_members:
            max_session_members = len(session.members)
    required_transport_boards = max(0, max_session_members - boards_at_location)
    transports_to = Transport.query.filter_by(practice_id=practice.id, direction='to').all()
    transports_from = Transport.query.filter_by(practice_id=practice.id, direction='from').all()
    all_boards = Board.query.order_by(Board.name).all()
    transported_to_board_ids = [t.board_id for t in transports_to]
    boards_at_practice = Board.query.filter((Board.location == practice.location) | (Board.id.in_(transported_to_board_ids))).all()
    return render_template('practice/detail.html', 
                           practice=practice, 
                           user_attendance=user_attendance, 
                           all_attendances=all_attendances,
                           boards_at_location=boards_at_location,
                           present_attendees=[att.user for att in all_attendances if att.status == 'present'],
                           assignable_attendees=assignable_attendees,
                           unassigned_attendees=unassigned_attendees,
                           max_session_members=max_session_members,
                           required_transport_boards=required_transport_boards,
                           transports_to=transports_to,
                           transports_from=transports_from,
                           all_boards=all_boards,
                           boards_at_practice=boards_at_practice)

@app.route('/practices/answer/<int:attendance_id>', methods=['POST'])
@login_required
@member_required
def answer_attendance(attendance_id):
    attendance = Attendance.query.get_or_404(attendance_id)
    if attendance.user_id != current_user.id:
        flash('権限がありません。', 'error')
        return redirect(url_for('practice_index'))
    attendance.status = request.form.get('status')
    attendance.notes = request.form.get('notes')
    attendance.reason = request.form.get('reason')
    db.session.commit()
    flash('出欠を更新しました。', 'success')
    return redirect(url_for('practice_detail', practice_id=attendance.practice_id))

@app.route('/practices/<int:practice_id>/add_session', methods=['POST'])
@login_required
@admin_required
def add_session(practice_id):
    practice = Practice.query.get_or_404(practice_id)
    session_count = len(practice.sessions)
    new_session = PracticeSession(practice_id=practice.id, session_number=session_count + 1)
    db.session.add(new_session)
    db.session.commit()
    flash(f'{session_count + 1}部を追加しました。', 'success')
    return redirect(url_for('practice_detail', practice_id=practice.id, _anchor='session-management'))

@app.route('/practices/assign_member', methods=['POST'])
@login_required
@admin_required
def assign_member():
    user_ids = request.form.getlist('user_ids')
    session_id = request.form.get('session_id')
    practice_id = request.form.get('practice_id')
    if not user_ids:
        flash('割り当てるメンバーが選択されていません。', 'error')
        return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='session-management'))
    session = PracticeSession.query.get(session_id)
    if not session:
        flash('セッションが見つかりません。', 'error')
        return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='session-management'))
    assigned_count = 0
    assigned_usernames = []
    for user_id in user_ids:
        user = User.query.get(user_id)
        if user and user not in session.members:
            session.members.append(user)
            assigned_count += 1
            assigned_usernames.append(user.username)
    if assigned_count > 0:
        db.session.commit()
        flash(f'{", ".join(assigned_usernames)} を{session.session_number}部に割り当てました。', 'success')
    else:
        flash('割り当てる新しいメンバーがいませんでした。', 'info')
    return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='session-management'))

@app.route('/practices/unassign_member/<int:session_id>/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def unassign_member(session_id, user_id):
    session = PracticeSession.query.get_or_404(session_id)
    user = User.query.get_or_404(user_id)
    if user in session.members:
        session.members.remove(user)
        db.session.commit()
        flash(f'{user.username}を{session.session_number}部から外しました。', 'success')
    return redirect(url_for('practice_detail', practice_id=session.practice_id, _anchor='session-management'))

@app.route('/practices/delete_session/<int:session_id>', methods=['POST'])
@login_required
@admin_required
def delete_session(session_id):
    session_to_delete = PracticeSession.query.get_or_404(session_id)
    practice_id = session_to_delete.practice_id
    db.session.delete(session_to_delete)
    db.session.commit()
    flash(f'{session_to_delete.session_number}部を削除しました。', 'success')
    return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='session-management'))

@app.route('/practices/delete/<int:practice_id>', methods=['POST'])
@login_required
@admin_required
def delete_practice(practice_id):
    practice_to_delete = Practice.query.get_or_404(practice_id)
    db.session.delete(practice_to_delete)
    db.session.commit()
    flash(f'練習「{practice_to_delete.title}」を削除しました。', 'success')
    return redirect(url_for('practice_index'))

@app.route('/practices/assign_transport', methods=['POST'])
@login_required
@admin_required
def assign_transport():
    practice_id = request.form.get('practice_id')
    user_id = request.form.get('user_id')
    board_ids = request.form.getlist('board_ids')
    direction = request.form.get('direction', 'to')
    if not all([practice_id, user_id, board_ids]):
        flash('運搬者とボードを選択してください。', 'error')
        return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))
    user = User.query.get(user_id)
    if not user:
        flash('ユーザーが見つかりません。', 'error')
        return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))
    for board_id in board_ids:
        existing = Transport.query.filter_by(practice_id=practice_id, board_id=board_id, direction=direction).first()
        if existing:
             # 上書き処理
            old_user = User.query.get(existing.user_id)
            if old_user:
                old_user.transport_count = max(0, old_user.transport_count - 1)
            existing.user_id = user_id
            user.transport_count += 1
        else:
            # 新規登録
            transport = Transport(practice_id=practice_id, user_id=user_id, board_id=board_id, direction=direction)
            db.session.add(transport)
            user.transport_count += 1
    db.session.commit()
    flash('運搬情報を登録しました。', 'success')
    return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))

@app.route('/practices/unassign_transport/<int:transport_id>', methods=['POST'])
@login_required
@admin_required
def unassign_transport(transport_id):
    transport_to_delete = Transport.query.get_or_404(transport_id)
    practice_id = transport_to_delete.practice_id
    user = User.query.get(transport_to_delete.user_id)
    if user:
        user.transport_count = max(0, user.transport_count - 1)
    db.session.delete(transport_to_delete)
    db.session.commit()
    flash('運搬情報を削除しました。', 'success')
    return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))

@app.route('/practices/<int:practice_id>/run_lottery', methods=['POST'])
@login_required
@admin_required
def run_lottery(practice_id):
    practice = Practice.query.get_or_404(practice_id)
    board_ids_for_lottery = request.form.getlist('board_ids_for_lottery')
    if not board_ids_for_lottery:
        flash('抽選対象のボードが選択されていません。', 'error')
        return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))
    boards_to_take_home_count = len(board_ids_for_lottery)
    attendees = {att.user for att in practice.attendances if att.status == 'present'}
    transports_to = Transport.query.filter_by(practice_id=practice.id, direction='to').all()
    transports_from_confirmed = Transport.query.filter_by(practice_id=practice.id, direction='from').all()
    transporters_to_users = {t.user for t in transports_to}
    transporters_from_confirmed_users = {t.user for t in transports_from_confirmed}
    primary_pool = list(attendees - transporters_to_users - transporters_from_confirmed_users)
    secondary_pool = list(transporters_to_users - transporters_from_confirmed_users)
    final_pool = []
    if len(primary_pool) >= boards_to_take_home_count:
        final_pool = primary_pool
    else:
        final_pool = primary_pool + secondary_pool
    if len(final_pool) < boards_to_take_home_count:
        flash(f'運搬可能な人数が足りません！(必要: {boards_to_take_home_count}人, 候補: {len(final_pool)}人)', 'error')
        return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))
    weights = [1 / ((user.transport_count + 1) ** 2) for user in final_pool]
    winners = []
    pool_with_weights = list(zip(final_pool, weights))
    for _ in range(boards_to_take_home_count):
        if not pool_with_weights: break
        users, current_weights = zip(*pool_with_weights)
        winner = random.choices(users, weights=current_weights, k=1)[0]
        winners.append(winner)
        pool_with_weights = [item for item in pool_with_weights if item[0].id != winner.id]
    for i, winner in enumerate(winners):
        board_id = board_ids_for_lottery[i]
        existing = Transport.query.filter_by(practice_id=practice_id, board_id=board_id, direction='from').first()
        if not existing:
            transport = Transport(practice_id=practice_id, user_id=winner.id, board_id=board_id, direction='from')
            db.session.add(transport)
            winner.transport_count += 1
    db.session.commit()
    winner_names = [w.username for w in winners]
    flash(f'抽選が完了し、{", ".join(winner_names)} が運搬者に自動で割り当てられました。', 'success')
    return redirect(url_for('practice_detail', practice_id=practice_id, _anchor='transport-planning'))


# --- Admin Routes ---
@app.route('/admin')
@login_required
@admin_required
def admin_panel():
    return render_template('admin/panel.html')

@app.route('/admin/teams', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_teams():
    if request.method == 'POST':
        team_name = request.form.get('team_name')
        if team_name:
            if Team.query.filter_by(name=team_name).first():
                flash('そのチーム名は既に使用されています。', 'error')
            else:
                new_team = Team(name=team_name)
                db.session.add(new_team)
                db.session.commit()
                flash(f'チーム「{team_name}」を追加しました。', 'success')
        else:
            flash('チーム名を入力してください。', 'error')
        return redirect(url_for('admin_teams'))
    teams = Team.query.order_by(Team.name).all()
    return render_template('admin/teams.html', teams=teams)

@app.route('/admin/teams/delete/<int:team_id>', methods=['POST'])
@login_required
@admin_required
def delete_team(team_id):
    team_to_delete = Team.query.get_or_404(team_id)
    if team_to_delete.users:
        flash('所属しているユーザーがいるため、このチームは削除できません。', 'error')
    else:
        db.session.delete(team_to_delete)
        db.session.commit()
        flash(f'チーム「{team_to_delete.name}」を削除しました。', 'success')
    return redirect(url_for('admin_teams'))

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/promote/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def promote_user(user_id):
    user_to_promote = User.query.get_or_404(user_id)
    user_to_promote.role = 'admin'
    db.session.commit()
    flash(f"ユーザー '{user_to_promote.username}' は管理者に昇格しました。", 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/demote/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def demote_user(user_id):
    if current_user.id == user_id:
        flash('自分自身を降格させることはできません。', 'error')
        return redirect(url_for('admin_users'))
    user_to_demote = User.query.get_or_404(user_id)
    user_to_demote.role = 'member'
    db.session.commit()
    flash(f"ユーザー '{user_to_demote.username}' は一般ユーザーに降格しました。", 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    if current_user.id == user_id:
        flash('自分自身を削除することはできません。', 'error')
        return redirect(url_for('admin_users'))
    
    user_to_delete = User.query.get_or_404(user_id)

    # 関連する子レコードを先に削除
    Announcement.query.filter_by(user_id=user_id).delete()
    Attendance.query.filter_by(user_id=user_id).delete()
    Transport.query.filter_by(user_id=user_id).delete()
    
    # ユーザーをセッションから削除
    for session in PracticeSession.query.all():
        if user_to_delete in session.members:
            session.members.remove(user_to_delete)

    # ユーザー本体を削除
    db.session.delete(user_to_delete)
    
    db.session.commit()
    flash(f"ユーザー '{user_to_delete.username}' と関連データをすべて削除しました。", 'success')
    return redirect(url_for('admin_users'))
@app.route('/admin/announcements')
@login_required
@admin_required
def admin_announcements():
    all_announcements = Announcement.query.order_by(Announcement.timestamp.desc()).all()
    return render_template('admin/announcements.html', announcements=all_announcements)

@app.route('/admin/announcements/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_announcement():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        if not title or not content:
            flash('タイトルと内容の両方を入力してください。', 'error')
            return redirect(url_for('new_announcement'))
        announcement = Announcement(title=title, content=content, user_id=current_user.id)
        db.session.add(announcement)
        db.session.commit()
        flash('新しいお知らせを投稿しました。', 'success')
        return redirect(url_for('admin_announcements'))
    return render_template('admin/new_announcement.html')

@app.route('/admin/announcements/delete/<int:announcement_id>', methods=['POST'])
@login_required
@admin_required
def delete_announcement(announcement_id):
    announcement_to_delete = Announcement.query.get_or_404(announcement_id)
    db.session.delete(announcement_to_delete)
    db.session.commit()
    flash('お知らせを削除しました。', 'success')
    return redirect(url_for('admin_announcements'))

if __name__ == '__main__':
    app.run(debug=True)


















