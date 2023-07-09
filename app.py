from sqlalchemy import desc, func
import json
import random
from flask_socketio import SocketIO, emit
from flask_moment import Moment
from flask_migrate import Migrate
from flask_login import LoginManager, login_required, current_user
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, current_app
import time
import os
from sys import platform
from dotenv import load_dotenv
from os import environ, path

DEV = platform == 'win32'

load_dotenv(path.join(path.abspath(path.dirname(__file__)), '.env'))
app = Flask(__name__)
app.secret_key = environ.get('sk')
moment = Moment(app)
socket_io = SocketIO(app, logger=True, engineio_logger=True, cors_allowed_origins="*")

from models import *

DIALECT = 'mysql'
DRIVER = 'pymysql'
USERNAME = 'poem_snake'
PASSWORD = environ.get('mysqlpassword')
HOST = '127.0.0.1'
PORT = '3306'
DATABASE = 'poem_snake'
app.config['SQLALCHEMY_DATABASE_URI'] = '{}+{}://{}:{}@{}:{}/{}?charset=utf8'.format(
    DIALECT, DRIVER, USERNAME, PASSWORD, HOST, PORT, DATABASE)
if DEV:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + app.root_path + '/data.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)

from announcement import announcement
from account import account, login_manager

app.register_blueprint(announcement)
app.register_blueprint(account)
login_manager.init_app(app)
login_manager.login_view = 'account.login'

users = []


@app.route('/')
def main():
    v = random.random()
    return render_template('index.html', v=v)


@socket_io.on('connect')
def connect():
    if current_user.is_authenticated:
        users.append(current_user.id)
    if not hasattr(current_app, 'game'):
        if Game.query.count() == 0:
            game_start()
        else:
            current_app.game = Game.query.order_by(desc(Game.id)).first()
            current_app.round = GameRound.query.filter_by(
                game_id=current_app.game.id).order_by(desc(GameRound.number)).first()
    emit('connect_message', {'message': 'Connected', 'current_game_content':
        json.dumps(current_app.game.info()), "current_round": json.dumps(current_app.round.info()),
                             'current_user': current_user.info() if current_user.is_authenticated else None})


@socket_io.on('disconnect')
def disconnect():
    if current_user.is_authenticated:
        users.remove(current_user.id)


@app.route('/api/users')
def get_users():
    return jsonify([User.query.filter_by(id=u).first().info() for u in users])


def game_start():
    content, origin, author = api.get_poem()
    game = Game()
    game.text = content
    game.title = origin
    game.author = author
    db.session.add(game)
    db.session.commit()
    current_app.game = game
    round = GameRound()
    round.text = game.cleared_text()[0]
    round.number = 0
    round.real_number = 0
    round.game = game
    db.session.add(round)
    db.session.commit()
    current_app.round = round
    emit("game_start", {'message': "新游戏开始",
                        'data': json.dumps(game.info())}, broadcast=True)


def round_start():
    game = current_app.game
    round = current_app.round
    if round.number == len(game.cleared_text()) - 1:
        time.sleep(5)
        emit("game_end", {'message': "游戏结束"}, broadcast=True)
        time.sleep(5)
        game_start()
        return
    else:
        roundnew = GameRound()
        roundnew.text = game.cleared_text()[round.number + 1]
        roundnew.number = round.number + 1
        if game.text[round.real_number + 1] == '，' or game.text[round.real_number + 1] == '？' or game.text[
            round.real_number + 1] == '。' or game.text[round.real_number + 1] == '！' or game.text[
            round.real_number + 1] == '。' or game.text[round.real_number + 1] == '，':
            roundnew.real_number = round.real_number + 2
        else:
            roundnew.real_number = round.real_number + 1
        roundnew.game = game
        db.session.add(roundnew)
        db.session.commit()
        current_app.round = roundnew
        emit("round_start", {'message': "新回合开始", 'data': json.dumps(
            roundnew.info())}, broadcast=True)


@socket_io.on('answer')
# @login_required
def answer(data):
    if not current_user.is_authenticated:
        emit("answer_check", {'message': "请先登录"})
        return
    text = data['data']
    r = Record()
    if len(api.clear_mark(text)) <= 8 or len(api.clear_mark(text)) >= 30:
        emit('answer_check', {'message': '长度不符合要求'})
        return
    w = text.find("（）")
    if w == -1:
        emit('answer_check', {'message': '没有找到括号'})
        return
    char = current_app.round.get_character()
    text = text[:w] + char + text[w + 2:]
    if api.clear_mark(current_app.game.text).find(api.clear_mark(text)) != -1 or api.clear_mark(text).find(
            api.clear_mark(current_app.game.text)) != -1:
        emit('answer_check', {'message': '发原诗，卡 bug？'})
        return
    if text[len(text) - 1] != '。' and text[len(text) - 1] != '？' and text[len(text) - 1] != '！' and text[
        len(text) - 1] != '；':
        emit('answer_check', {'message': '末尾需要有标点符号'})
        return
    try:
        check = api.search_poem(text)
    except Exception as e:
        print(e)
        emit("answer_check", {'message': '出错了，大概率找不到这句诗'})
        return
    if check:
        if not check.is_valid():
            emit('answer_check', {'message': check.error_msg()})
            return
        r.line = check.content
        r.title = check.title
        r.author = check.author
        r.user = current_user
        r.game = current_app.game
        r.gameround = current_app.round
        current_user.coin = current_user.get_coin() + 1
        db.session.add(r)
        db.session.commit()
        # round_start()
        emit('answer_check', {'message': '提交成功', 'data': json.dumps({
            'title': r.title, 'author': r.author})})
        emit('record_add', {'message': '已有人答出',
                            'data': json.dumps(r.info())}, broadcast=True)
        round_start()
    else:
        emit('answer_check', {'message': '没有找到这句诗'})


@socket_io.on('test')
def test():
    emit('test', {'game': json.dumps(current_app.game.info()),
                  'round': json.dumps(current_app.round.info())})


@app.route('/api/history')
# @login_required
def history():
    last = request.args.get('last', 19260817, type=int)
    records = Record.query.filter(Record.id < last).order_by(
        desc(Record.id)).limit(10).all()
    return jsonify([r.info() for r in records])


@app.route('/api/ranklist')
def ranklist():
    perpage = request.args.get('perpage', 10, type=int)
    page = request.args.get('page', 1, type=int)
    users = User.query.join(Record, Record.user_id == User.id).with_entities(User.id, User.username, User.email,
                                                                             func.count(Record.id),
                                                                             User.avatar_uploaded).group_by(
        User.id).order_by(
        desc(func.count(Record.id))).paginate(page, perpage, False)
    first = (page - 1) * perpage + 1
    return jsonify({'page': page, "perpage": perpage, 'data': [
        {"num": first + idx, "uid": u[0], "username": u[1], 'count': u[3],
         'gravatar': Gravatar(u[2]).get_image(default='identicon').replace('www.gravatar.com',
                                                                           'gravatar.rotriw.com')
         if not u[4] else f'/static/avatars/{u[0]}.png'} for
        idx, u in enumerate(users.items)]})


@app.route('/api/coin')
@login_required
def coin():
    return jsonify({'coin': current_user.get_coin()})


@app.route('/api/skipcheck')
@login_required
def skipcheck():
    if current_user.admin or current_user.get_coin() >= 50:
        return jsonify(True)
    else:
        return jsonify(False)


@socket_io.on('skip')
@login_required
def skip():
    if current_user.admin:
        emit('skip_check', {'status': 'success', 'message': '管理员跳过'})
        round_start()
    elif current_user.get_coin() >= 50:
        current_user.coin -= 50
        db.session.commit()
        emit('skip_check', {'status': 'success', 'message': '花费 50 金币，剩余 {}'.format(current_user.get_coin())})
        round_start()
    else:
        emit('skip_check', {'status': 'error', 'message': '金币不足，剩余 {}'.format(current_user.get_coin())})


@socket_io.on('talk_message')
def talk_message(data):
    socket_io.emit('talk', {'message': data, 'user': json.dumps(current_user.info())})


@app.route('/api/upload', methods=['POST'])
def upload_avatar():
    class AvatarForm(FlaskForm):
        avatar = FileField(validators=[FileRequired(), FileAllowed(['png', 'jpg'], 'Images only!')])

    form = AvatarForm(meta={'csrf': False})
    app.logger.info(request.files)
    if form.validate_on_submit():
        if not current_user.is_authenticated:
            return jsonify({'status': 'error', 'message': '请先登录'})
        filename = str(current_user.id) + '.png'
        try:
            form.avatar.data.save(os.path.join('./static/avatars/', filename))
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})
        current_user.avatar_uploaded = False
        db.session.commit()
        return jsonify({'status': 'success', 'message': '上传成功'})
    else:
        return jsonify({'status': 'error', 'message': form.errors})


if __name__ == '__main__':
    if DEV:
        socket_io.run(app)
    else:
        socket_io.run(app, host='0.0.0.0', port=19999)
