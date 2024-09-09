from flask import Blueprint, url_for, render_template, request, jsonify, g, flash, current_app
from datetime import datetime, timedelta
from sqlalchemy import func
from werkzeug.utils import redirect
from .. import db
from ..forms import ContentForm
from ..models import Content, Evaluation, User, content_voter, Option, Topic, UserImage
from .auth_views import login_required
from .. import csrf
from .chatgpt_views import get_setting_data
from pathlib import Path
import os

bp = Blueprint('content', __name__, url_prefix='/content')
timeLeft = 120

@bp.route('/list')
def _list():
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  #정렬
  if so == 'recommend':
    sub_query = db.session.query(
      content_voter.c.content_id, func.count('*').label('num_voter'))\
      .group_by(content_voter.c.content_id).subquery()
    content_list = Content.query\
      .outerjoin(sub_query, Content.id == sub_query.c.content_id)\
      .filter(Content.topic_id.is_(None))\
      .order_by(sub_query.c.num_voter.desc(), Content.create_date.desc())
  elif so == 'popular':
    sub_query = db.session.query(
      Evaluation.content_id, func.count('*').label('num_eval'))\
      .group_by(Evaluation.content_id).subquery()
    content_list = Content.query\
      .outerjoin(sub_query, Content.id == sub_query.c.content_id)\
      .filter(Content.topic_id.is_(None))\
      .order_by(sub_query.c.num_eval.desc(), Content.create_date.desc())
  else:
    content_list = Content.query.filter(Content.topic_id.is_(None)).order_by(Content.create_date.desc())

  #조회
  # content_list = Content.query.order_by(Content.create_date.desc())
  if kw:
    search = '%%{}%%'.format(kw)
    sub_query = db.session.query(Evaluation.content_id, Evaluation.comment, User.username).join(User, Evaluation.user_id == User.id).subquery()
    content_list = content_list.outerjoin(sub_query, sub_query.c.content_id == Content.id).filter(
      Content.question.ilike(search) |
      Content.answer.ilike(search) | 
      Content.class_name.ilike(search) |
      User.username.ilike(search) | 
      sub_query.c.comment.ilike(search)
    ).distinct()
  content_list = content_list.paginate(page=page, per_page=10)
  return render_template('content/content_list.html', content_list=content_list, page=page, kw=kw, so=so)

@bp.route('/detail/<int:content_id>')
def detail(content_id):
  form = ContentForm()
  content = Content.query.get_or_404(content_id)

  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  #정렬
  if so == 'recommend':
    sub_query = db.session.query(
      content_voter.c.content_id, func.count('*').label('num_voter'))\
      .group_by(content_voter.c.content_id).subquery()
    content_list = Content.query\
      .outerjoin(sub_query, Content.id == sub_query.c.content_id)\
      .filter(Content.topic_id.is_(None))\
      .order_by(sub_query.c.num_voter.desc(), Content.create_date.desc())
  elif so == 'popular':
    sub_query = db.session.query(
      Evaluation.content_id, func.count('*').label('num_eval'))\
      .group_by(Evaluation.content_id).subquery()
    content_list = Content.query\
      .outerjoin(sub_query, Content.id == sub_query.c.content_id)\
      .filter(Content.topic_id.is_(None))\
      .order_by(sub_query.c.num_eval.desc(), Content.create_date.desc())
  else:
    content_list = Content.query.filter(Content.topic_id.is_(None)).order_by(Content.create_date.desc())

  #조회
  # content_list = Content.query.order_by(Content.create_date.desc())
  if kw:
    search = '%%{}%%'.format(kw)
    sub_query = db.session.query(Evaluation.content_id, Evaluation.comment, User.username).join(User, Evaluation.user_id == User.id).subquery()
    content_list = content_list.outerjoin(sub_query, sub_query.c.content_id == Content.id).filter(
      Content.question.ilike(search) |
      Content.answer.ilike(search) | 
      Content.class_name.ilike(search) |
      User.username.ilike(search) | 
      sub_query.c.comment.ilike(search)
    ).distinct()
  content_list = content_list.paginate(page=page, per_page=10)
  return render_template('content/content_detail.html', content=content, form=form, content_list=content_list, page=page, kw=kw, so=so)

@bp.route('/create/<int:device_id>', methods=('POST',))
@csrf.exempt
def create(device_id):
  setting = get_setting_data(device_id)
  form = ContentForm()
  if form.validate_on_submit():
    try:
      if setting.get('not_upload'):
        item_to_delete = UserImage.query.join(User).filter(
          User.username == form.class_name.data, 
          UserImage.content_id == None, 
        ).first()
        if item_to_delete:
          item_path = Path(current_app.config["UPLOAD_FOLDER"], Path(item_to_delete.imagePath).name)
          if item_path.exists():
            os.remove(item_path)  # 폴더에서 이미지 파일 삭제  
          db.session.delete(item_to_delete)
          db.session.commit()
        return jsonify({'message': '요청이 정상적으로 처리되었습니다.'}), 200

      now = datetime.now()
      content = None

      if not setting.get('topic'):
        content = Content(
          question=form.question.data, 
          answer=form.answer.data,
          class_name=form.class_name.data, 
          create_date=datetime.now())
        db.session.add(content)
        db.session.commit()
        
        lastest = Content.query.filter(Content.topic_id.is_(None)).order_by(Content.create_date.desc()).first()
        now = datetime.now()
        ago = now - timedelta(minutes=timeLeft)
        image_pending = UserImage.query.join(User).filter(
          User.username == form.class_name.data, 
          UserImage.content_id == None, 
          UserImage.create_date > ago
        ).first()
        if image_pending:
          image_pending.content_id = lastest.id
          db.session.commit()

      else:
        topic_title = setting.get('topic')
        _topic = Topic.query.filter_by(deviceId=device_id, title=topic_title).order_by(Topic.create_date.desc()).first()
        content = Content(
          question=form.question.data, 
          answer=form.answer.data,
          class_name=form.class_name.data,
          topic = _topic, 
          create_date=now
        )
        db.session.add(content)
        db.session.commit()
      
      if content:
        option = Option(
          content = content,
          max_tokens = setting.get('max_tokens'),
          temperature = setting.get('temperature'),
          top_p = setting.get('top_p'),
          gpt_role = setting.get('gpt_role')
        )
        db.session.add(option)
        db.session.commit()

      return jsonify({'message': '요청이 정상적으로 처리되었습니다.'}), 200
    
    except Exception as e:
      db.session.rollback()
      return jsonify({'message': f'오류 발생: {str(e)}'}), 500
  
  error_messages = '\n'.join([f'{field}: {", ".join(errors)}' for field, errors in form.errors.items()])
  return jsonify({'message': f'폼이 올바르게 제출되지 않았습니다. 다음 오류를 확인하세요:\n{error_messages}'}), 400

@bp.route('/modify/<int:content_id>', methods=('GET', 'POST'))
@login_required
def modify(content_id):
  content = Content.query.get_or_404(content_id)
  topicId = content.topic_id
  if g.user.id != 1:
    flash('수정권한이 없습니다')
    return redirect('{}#content_{}'.format(
      url_for('topic.detail', topic_id=topicId), content.id
    ))
  if request.method == "POST":
    form = ContentForm()
    if form.validate_on_submit():
      form.populate_obj(content)
      content.modify_date = datetime.now()
      db.session.commit()
      return redirect('{}#content_{}'.format(
        url_for('topic.detail', topic_id=topicId), content.id
      ))
  else:
    form = ContentForm(obj=content)
  return render_template('content/content_form.html', content=content, form=form)

@bp.route('/delete/<int:content_id>')
@login_required
def delete(content_id):
  content = Content.query.get_or_404(content_id)
  if g.user.id != 1:
    flash('삭제권한이 없습니다')
    return redirect(url_for('content.detial', content_id=content_id))
  
  if hasattr(content, 'userImage_set') and content.userImage_set:
    item = content.userImage_set[0]
    item_path = Path(current_app.config["UPLOAD_FOLDER"], Path(item.imagePath).name)
    if item_path.exists():
      os.remove(item_path)  # 폴더에서 이미지 파일 삭제  
  db.session.delete(content)
  db.session.commit()

  return redirect(url_for('content._list'))

@bp.route('/db_to_file/')
@login_required
def db_to_file():
  records = Content.query.filter(Content.class_name != '과학쌤', Content.class_name != '운영자', Content.class_name != '과학실', Content.topic_id.is_(None)).all()
  try:
    with open("questions.txt", "a", encoding="utf-8") as txt_file:
      for record in records:
        txt_file.write(record.question + "\n")
      print("txt_file_created!")
  except IOError as e:
    print(f"An error occured: {e}")
  return redirect(url_for('content._list'))
