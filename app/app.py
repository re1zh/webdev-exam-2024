import datetime
from io import BytesIO
from functools import wraps

import mysql.connector as connector
from flask import Flask, render_template, session, request, redirect, url_for, flash, current_app, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required

from mysqldb import DBConnector

app = Flask(__name__)
application = app
app.config.from_pyfile('config.py')

db_connector = DBConnector(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth'
login_manager.login_message_category = 'warning'

class User(UserMixin):
    def __init__(self, user_id, user_login, user_role):
        self.id = user_id
        self.user_login = user_login
        self.role_id = user_role

    def is_admin(self):
        return self.id_role == current_app.config['ADMIN_ROLE_ID']

    def is_moderator(self):
        return self.id_role == current_app.config['MODERATOR_ROLE_ID']

    # def can(self, action, user=None):
    #     policy = UsersPolicy(user)
    #     return getattr(policy, action, lambda: False)()

def db_operation(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time, end_time = None, None
        connection = db_connector.connect()
        try:
            start_time = datetime.datetime.now()
            with connection.cursor(named_tuple=True, buffered=True) as cursor:
                result = func(cursor, *args, **kwargs)
                connection.commit()
        except Exception as e:
            connection.rollback()
            raise e
        finally:
            end_time = datetime.datetime.now()
            print(f"Duration {func}: {end_time - start_time}")
            # connection.close()
        return result
    return wrapper


@login_manager.user_loader
def load_user(user_id):
    with db_connector.connect().cursor(named_tuple=True) as cursor:
        cursor.execute("SELECT id, login, role_id FROM user WHERE id = %s;", (user_id,))
        user = cursor.fetchone()
    if user is not None:
        return User(user.id, user.login, user.role_id)
    return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/login', methods=['POST', 'GET'])
@db_operation
def login(cursor):
    error = ''
    if request.method == 'POST':
        login = request.form['username']
        password = request.form['password']

        if login and password:
            remember_me = request.form.get('remember_me', None) == 'on'
            cursor.execute("SELECT id, login, role_id FROM user WHERE login = %s AND password_hash = SHA2(%s, 256)",
                (login, password))
            user = cursor.fetchone()

            if user is not None:
                flash('Авторизация прошла успешно', 'success')
                login_user(User(user.id, user.login, user.role_id), remember=remember_me)
                return redirect(url_for('index'))
            else:
                flash('Неправильный логин или пароль', 'danger')
        else:
            flash('Заполните все поля', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)