from flask import Blueprint, url_for, render_template, flash, request, session, g
from werkzeug.utils import redirect
import hashlib
import functools

from voiceGPT import db
from voiceGPT.forms import UserCreateForm, UserLoginForm
from voiceGPT.models import User

bp = Blueprint('auth', __name__, url_prefix='/auth')

@bp.route('/signup/', methods=('GET', 'POST'))
def signup():
  form = UserCreateForm()
  if request.method == 'POST' and form.validate_on_submit():
    existing_user = User.query.filter_by(username=form.username.data).first()
    if existing_user:
      flash('이미 존재하는 사용자입니다.')
      return redirect(url_for('auth.signup'))
    
    hashed_password = hashlib.sha256(form.password1.data.encode('utf-8')).hexdigest()
    new_user = User(username=form.username.data, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()
    flash('회원가입이 완료되었습니다. 로그인해주세요.')
    return redirect(url_for('main.index'))
  return render_template('auth/signup.html', form=form)

@bp.route('/login/', methods=('GET', 'POST'))
def login():
  form = UserLoginForm()
  if request.method == 'POST' and form.validate_on_submit():
    error = None
    user = User.query.filter_by(username=form.username.data).first()
    if not user:
      error = "존재하지 않는 사용자입니다."
    elif not user.password == hashlib.sha256(form.password.data.encode('utf-8')).hexdigest():
      error = "비밀번호가 올바르지 않습니다."
    if error is None:
      session.clear()
      session['user_id'] = user.id
      return redirect(url_for('main.index'))
    flash(error)
  return render_template('auth/login.html', form=form)

@bp.before_app_request
def load_logged_in_user():
  user_id = session.get('user_id')
  if user_id is None:
    g.user = None
  else:
    g.user = User.query.get(user_id)

@bp.route('/logout')
def logout():
  session.clear()
  return redirect(url_for('main.index'))

def login_required(view):
  @functools.wraps(view)
  def wrapped_view(**kwargs):
    if g.user is None:
      return redirect(url_for('auth.login'))
    return view(**kwargs)
  return wrapped_view
    
@bp.route('/modify/<int:user_id>', methods=['GET', 'POST'])
@login_required
def modify(user_id):
  if g.user.id != user_id:
    flash('수정권한이 없습니다.')
    return redirect(url_for('main.index'))
  
  user = User.query.get_or_404(user_id)
  form = UserCreateForm()

  if request.method == 'POST':
    if form.validate_on_submit():
      existing_user = User.query.filter_by(username=form.username.data).first()
      if existing_user and existing_user.id != user.id:
        flash('입력하신 이름의 사용자가 이미 있습니다.')
        return redirect(url_for('main.index'))
    
      user.username = form.username.data
      if form.password1.data:  # Only change password if it's provided
        hashed_password = hashlib.sha256(form.password1.data.encode('utf-8')).hexdigest()
        user.password = hashed_password
        db.session.commit()  # Save changes to the database
        flash('회원 정보를 변경했습니다. 다시 로그인해 주세요.')
        return redirect(url_for('auth.logout'))
      else:
        flash('폼 데이터가 유효하지 않습니다.')
  return render_template('auth/modify.html', form=form, user=user)