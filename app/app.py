import datetime
from io import BytesIO
from math import ceil
from functools import wraps
import os

import mysql.connector as connector
from flask import Flask, render_template, session, request, redirect, url_for, flash, current_app, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.utils import secure_filename

import bleach
import hashlib
import markdown

from mysqldb import DBConnector
from user_policy import UsersPolicy

app = Flask(__name__)
application = app
app.config.from_pyfile('config.py')

db_connector = DBConnector(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth'
login_manager.login_message_category = 'warning'


MAX_PER_PAGE = 10


class User(UserMixin):
    def __init__(self, user_id, user_login, user_role):
        self.id = user_id
        self.user_login = user_login
        self.role_id = user_role

    def is_admin(self):
        return self.role_id == current_app.config['ADMIN_ROLE_ID']

    def is_moderator(self):
        return self.role_id == current_app.config['MODERATOR_ROLE_ID']

    def can(self, action, user=None):
        policy = UsersPolicy(user)
        return getattr(policy, action, lambda: False)()


def db_operation(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time, end_time = None, None
        connection = db_connector.connect()
        try:
            start_time = datetime.datetime.now()
            cursor = connection.cursor(buffered=True, named_tuple=True)
            result = func(cursor, *args, **kwargs)
            connection.commit()
        except Exception as e:
            connection.rollback()
            raise e
        finally:
            end_time = datetime.datetime.now()
            print(f"Duration {func}: {end_time - start_time}")
            #connection.close()
        return result
    return wrapper


@login_manager.user_loader
@db_operation
def load_user(cursor, user_id):
    cursor.execute("SELECT id, login, role_id FROM user WHERE id = %s;", (user_id,))
    user = cursor.fetchone()
    if user is not None:
        return User(user.id, user.login, user.role_id)
    return None


def check_for_privelege(action):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            user = None
            if 'user_id' in kwargs.keys():
                with db_connector.connect().cursor(named_tuple=True) as cursor:
                    cursor.execute("SELECT * FROM users WHERE id = %s;", (kwargs.get('user_id'),))
                    user = cursor.fetchone()
            if not (current_user.is_authenticated and current_user.can(action, user)):
                flash('Недостаточно прав для доступа к этой странице', 'warning')
                return redirect(url_for('users.index'))
            return function(*args, **kwargs)
        return wrapper
    return decorator


@db_operation
def get_date(cursor):
    query = (
        "SELECT now() as date "
    )
    cursor.execute(query)
    date = cursor.fetchone()
    return date


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


@app.route('/books')
@db_operation
def books(cursor):
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * MAX_PER_PAGE

    query = (f"""
        SELECT book.id, book.name
            , group_concat(distinct g.name separator ', ') AS genres
            , book.year
            , round(avg(review.score)) AS avg_score
            , count(review.id) AS review_qty
            , cover.file_name
            , cover.mime_type
        FROM book
        LEFT JOIN book_genre bg ON book.id = bg.book_id
        LEFT JOIN genre g ON bg.genre_id = g.id
        LEFT JOIN review ON book.id = review.book_id
        LEFT JOIN cover ON book.cover_id = cover.id
        GROUP BY book.id, book.name, book.year, cover.file_name, cover.mime_type
        ORDER BY book.year desc
        LIMIT %s OFFSET %s
    """)
    cursor.execute(query, (MAX_PER_PAGE, offset))
    books = cursor.fetchall()

    count_query = (f"""
        SELECT COUNT(DISTINCT book.id) AS count
        FROM book
        JOIN book_genre bg ON book.id = bg.book_id
        JOIN genre g ON bg.genre_id = g.id
    """)
    cursor.execute(count_query)
    record_count = cursor.fetchone().count

    page_count = ceil(record_count / MAX_PER_PAGE)
    pages = range(max(1, page - 3), min(page_count, page + 3) + 1)

    return render_template('books.html', books=books,
                           page=page, pages=pages, page_count=page_count)


def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@db_operation
def insert_cover(cursor, file):
    filename = secure_filename(file.filename)
    if not allowed_file(filename):
        flash('Недопустимый формат файла', 'danger')

    file_data = file.read()
    md5_hash = hashlib.md5(file_data).hexdigest()
    mime_type = filename.rsplit('.', 1)[1].lower()

    try:
        query = (f"""
            SELECT id, file_name, mime_type
            FROM cover
            WHERE md5_hash = %s
        """)
        cursor.execute(query, [md5_hash])
        existing_cover = cursor.fetchone()

        if existing_cover:
            return existing_cover.id, file_data, existing_cover.mime_type

        query = (f"""
            INSERT INTO cover (file_name, mime_type, md5_hash) VALUES
            (%s, %s, %s)
        """)

        cursor.execute(query, (md5_hash, mime_type, md5_hash))
        cover_id = cursor.lastrowid
        new_file_name = f'{cover_id}'

        query = (f"""
            UPDATE cover SET file_name = %s WHERE id = %s
        """)
        cursor.execute(query, (new_file_name, cover_id))
        file.seek(0)
        return cover_id, file_data, mime_type
    except connector.errors.DatabaseError as error:
        flash(f'Произошла ошибка при создании записи: {error}', 'danger')
        raise error


@app.route('/create_book', methods=['POST', 'GET'])
@db_operation
@check_for_privelege('create_book')
def create_book(cursor):
    if request.method == 'POST':
        try:
            name = request.form['name']
            description = request.form['description']
            year = request.form['year']
            publisher = request.form['publisher']
            author = request.form['author']
            volume = request.form['volume']
            genre_ids = request.form.getlist('genres')
            cover = request.files['cover']

            if cover and cover.filename != '':
                cover_id, cover_data, mime_type = insert_cover(cover)
            else:
                cover_id = None

            sanitized_desc = bleach.clean(description)

            query = (f"""
                INSERT INTO book (name, description, year, publisher, author, volume, cover_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """)

            cursor.execute(query, (name, sanitized_desc, year, publisher, author, volume, cover_id))
            print('Запись вставлена в таблицу book')

            book_id = cursor.lastrowid

            for genre_id in genre_ids:
                cursor.execute(f"""
                        INSERT INTO book_genre (book_id, genre_id)
                        VALUES (%s, %s)
                    """, (book_id, int(genre_id)))
                print(int(genre_id))

            if cover_data:
                cover_path = os.path.join('static/img', f'{cover_id}.{mime_type}')
                with open(cover_path, 'wb') as f:
                    f.write(cover_data)

            flash('Книга успешно добавлена', 'success')
            return redirect(url_for('view_book', id_book=book_id))

        except connector.errors.DatabaseError as error:
            flash(f'При сохранении данных возникла ошибка. Проверьте корректность введённых данных: {error}', 'danger')

    query = (f"""
        SELECT * FROM genre
    """)
    cursor.execute(query)
    genres = cursor.fetchall()

    return render_template('create_book.html', genres=genres)


@app.route('/<int:id_book>/view_book')
@db_operation
def view_book(cursor, id_book):
    try:
        query = (f"""
            SELECT * FROM book WHERE id = %s
        """)
        cursor.execute(query, [id_book])
        book = cursor.fetchone()

        if not book:
            flash(f'Такой книги нет в базе данных', 'danger')

        query = (f"""
            SELECT review.*, user.surname, user.name, user.patronymic
            FROM review
            JOIN user
            ON review.user_id = user.id
            WHERE book_id = %s
        """)
        cursor.execute(query, [id_book])
        reviews = cursor.fetchall()

        query = (f"""
            SELECT genre_id FROM book_genre WHERE book_id = %s
        """)
        cursor.execute(query, [id_book])
        genre_ids = [row.genre_id for row in cursor.fetchall()]

        if genre_ids:
            genre_placeholders = ', '.join(['%s'] * len(genre_ids))

            query = (f"""
                SELECT name FROM genre WHERE id IN ({genre_placeholders})
            """)
            cursor.execute(query, genre_ids)
            genres = ", ".join([row.name for row in cursor.fetchall()])
        else:
            genres = ""


        if book.cover_id:
            query = (f"""
                SELECT file_name, mime_type FROM cover WHERE id = %s
            """)
            cursor.execute(query, [book.cover_id])
            cover_data = cursor.fetchone()
        else:
            cover_data = None

        if current_user.is_authenticated:
            query = (f"""
                SELECT * FROM review WHERE book_id = %s AND user_id = %s
            """)
            cursor.execute(query, (id_book, current_user.id))
            user_review = cursor.fetchone()
        else:
            user_review = None

    except connector.errors.DatabaseError as error:
        flash(f'Ошибка при получении данных книги: {error}', 'danger')
        return redirect(url_for('books'))
    return render_template('view_book.html', book=book, genres=genres,
        cover_data=cover_data, user_review=user_review, reviews=reviews)


@app.route('/<int:id_book>/edit_book', methods=['GET', 'POST'])
@db_operation
@login_required
@check_for_privelege('edit_book')
def edit_book(cursor, id_book):
    if request.method == 'POST':
        try:
            query = (f"""
                    SELECT * FROM book WHERE id = %s
                """)
            cursor.execute(query, [id_book])
            book = cursor.fetchone()

            if not book:
                flash(f'Такой книги нет в базе данных', 'danger')

            name = request.form['name']
            description = request.form['description']
            year = request.form['year']
            publisher = request.form['publisher']
            author = request.form['author']
            volume = request.form['volume']
            genre_ids = request.form.getlist('genres')

            sanitized_desc = bleach.clean(description)

            query = (f"""
                UPDATE book SET name = %s, description = %s, year = %s, publisher = %s, author = %s, volume = %s WHERE id = %s
            """)
            cursor.execute(query, (name, sanitized_desc, year, publisher, author, volume, id_book))
            print(f'Данные книги {name} обновлены')

            query = (f"""
                DELETE FROM book_genre WHERE book_id = %s
            """)
            cursor.execute(query, [id_book])
            for genre_id in genre_ids:
                cursor.execute(
                    "INSERT INTO book_genre (book_id, genre_id) VALUES (%s, %s)",
                    (id_book, int(genre_id))
                )
            flash('Книга успешно обновлена', 'success')
            return redirect(url_for('books'))

        except connector.errors.DatabaseError as error:
            flash(f'При изменении данных возникла ошибка. Проверьте корректность введённых данных: {error}', 'danger')

    query = (f"""
            SELECT * FROM genre
        """)
    cursor.execute(query)
    genres = cursor.fetchall()

    query = (f"""
        SELECT genre_id FROM book_genre WHERE book_id = %s
    """)
    cursor.execute(query, [id_book])
    book_genres = [bg.genre_id for bg in cursor.fetchall()]

    return render_template('edit_book.html', book=book, genres=genres, book_genres=book_genres)


@app.route('/books/<int:id_book>/delete_book', methods=['POST','GET', 'DELETE'])
@db_operation
@login_required
@check_for_privelege('delete_book')
def delete_book(cursor, id_book):
    try:
        query = (f"""
            SELECT * FROM book WHERE id = %s
        """)
        cursor.execute(query, [id_book])
        book = cursor.fetchone()

        if not book:
            flash(f'Такой книги нет в базе данных', 'danger')
        cover_id = book.cover_id

        query = (f"""
            DELETE FROM book WHERE id = %s
        """)
        cursor.execute(query, [id_book])
        print(f'Книга {book.name} удалена')

        if cover_id:
            query = (f"""
                SELECT file_name, mime_type FROM cover WHERE id = %s
            """)
            cursor.execute(query, [cover_id])
            cover = cursor.fetchone()
            if cover:
                cover_path = os.path.join('static/img', f"{cover.file_name}.{cover.mime_type}")
                if os.path.exists(cover_path):
                    os.remove(cover_path)

                query = (f"""
                    DELETE FROM cover WHERE id = %s
                """)
                cursor.execute(query, [cover_id])
                print(f'Обложка {cover_id} удалена')

        flash('Книга успешно удалена', 'success')
    except connector.errors.DatabaseError as error:
        flash(f'Ошибка при удалении книги: {error}', 'danger')

    return redirect(url_for('books'))


@app.route('/<int:id_book>/create_review', methods=['GET', 'POST'])
@db_operation
@login_required
@check_for_privelege('create_review')
def create_review(cursor, id_book):
    try:
        query = (f"""
            SELECT * FROM book WHERE id = %s
        """)
        cursor.execute(query, [id_book])
        book = cursor.fetchone()

        if not book:
            flash(f'Такой книги нет в базе данных', 'danger')

        query = (f"""
            SELECT * FROM review WHERE book_id = %s AND user_id = %s
        """)
        cursor.execute(query, (id_book, current_user.id))
        existing_review = cursor.fetchone()

        if existing_review:
            flash('Вы уже написали рецензию на эту книгу', 'warning')
            return redirect(url_for('view_book', id_book=id_book))

        if request.method == 'POST':
            score = request.form['score']
            text = request.form['text']

            sanitized_text = bleach.clean(text)

            query = (f"""
                INSERT INTO review (book_id, user_id, score, text, created_at)
                VALUES (%s, %s, %s, %s, %s)
            """)
            cursor.execute(query, (id_book, current_user.id, score, sanitized_text, get_date().date))
            print('Рецензия вставлена')

            flash('Рецензия успешно добавлена!', 'success')
            return redirect(url_for('view_book', id_book=id_book))

    except connector.errors.DatabaseError as error:
        flash(f'Ошибка при добавлении рецензии: {error}', 'danger')

    return render_template('create_review.html')


if __name__ == '__main__':
    app.run(debug=True)