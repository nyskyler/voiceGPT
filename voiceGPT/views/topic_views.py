from flask import Blueprint, url_for, render_template, request, jsonify, g, flash
from datetime import datetime
from sqlalchemy import func
from werkzeug.utils import redirect
from .. import db
from ..forms import ContentForm
from ..models import Content, Evaluation, User, content_voter, Option, Topic
from .auth_views import login_required
from .. import csrf
from .chatgpt_views import get_setting_data

bp = Blueprint('topic', __name__, url_prefix='/topic')

def voter_count(topic_id):
  # 특정 topic_id를 갖는 모든 Content 객체들의 voter 숫자를 계산
  voter_count_query = (
    db.session.query(func.count(content_voter.c.user_id))
    .join(Content, content_voter.c.content_id == Content.id)
    .filter(Content.topic_id == topic_id)
  ).scalar()
  return voter_count_query
    

@bp.route('/list')
def _list():
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  topic_list = Topic.query.order_by(Topic.create_date.desc())
  topic_list = topic_list.paginate(page=page, per_page=10)
  voter_count_subquery = (
    db.session.query(
      Content.topic_id,
      func.count(content_voter.c.user_id).label('voter_count')
    )
    .join(Content, content_voter.c.content_id == Content.id)
    .group_by(Content.topic_id)
    .subquery()
  )

  # Join the main query with the subquery
  topics_with_voter_count = (
    db.session.query(Topic, voter_count_subquery.c.voter_count)
    .outerjoin(voter_count_subquery, Topic.id == voter_count_subquery.c.topic_id)
    .filter(Topic.id.in_([t.id for t in topic_list.items]))
    .all()
  )

  # Recreate the topic_list with the voter counts
  updated_topic_list = []
  for topic, voter_count in topics_with_voter_count:
    topic.voter_count = voter_count or 0
    updated_topic_list.append(topic)

  return render_template('topic/topic_list.html', topic_list=topic_list, page=page, kw=kw, so=so, updated_topic_list=updated_topic_list)

@bp.route('/detail/<int:topic_id>')
def detail(topic_id):
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='registration')

  form = ContentForm()
  topic = Topic.query.get_or_404(topic_id)
  contents = Content.query.filter_by(topic_id=topic_id).order_by(Content.create_date.asc()).all()
  first_content = contents[0] if contents else None
  voter_count_value = voter_count(topic_id)

  if so == 'recommend':
    sub_query = db.session.query(
      content_voter.c.content_id, func.count('*').label('num_voter'))\
      .group_by(content_voter.c.content_id).subquery()
    contents = Content.query\
      .outerjoin(sub_query, Content.id == sub_query.c.content_id)\
      .filter(Content.topic_id == topic_id)\
      .order_by(sub_query.c.num_voter.desc(), Content.create_date.desc())
  elif so == 'popular':
    sub_query = db.session.query(
      Evaluation.content_id, func.count('*').label('num_eval'))\
      .group_by(Evaluation.content_id).subquery()
    contents = Content.query\
      .outerjoin(sub_query, Content.id == sub_query.c.content_id)\
      .filter(Content.topic_id == topic_id)\
      .order_by(sub_query.c.num_eval.desc(), Content.create_date.desc())
  # else:
  #   content_list = Content.query.filter(Content.topic_id.is_(None)).order_by(Content.create_date.desc())

  if kw:
    search = '%%{}%%'.format(kw)

    sub_query = db.session.query(
      Evaluation.content_id, Evaluation.comment, User.username)\
      .join(User, Evaluation.user_id == User.id).subquery()
    contents = Content.query.join(Topic)\
      .filter(Content.topic_id == topic_id)\
      .outerjoin(sub_query, sub_query.c.content_id == Content.id).filter(
        Content.class_name.ilike(search) |
        Content.question.ilike(search) |
        Content.answer.ilike(search) |
        sub_query.c.comment.ilike(search) |
        sub_query.c.username.ilike(search)
      ).distinct()

  return render_template('topic/topic_detail.html', contents=contents, content=first_content if first_content else None, topic=topic, form=form, voter_count=voter_count_value, kw=kw, so=so)
  
@bp.route('/modify/<int:topic_id>', methods=('POST',))
@login_required
def modify(topic_id):
  topic = Topic.query.get_or_404(topic_id)
  if g.user.id != 1:
    flash('수정권한이 없습니다')
    return redirect(url_for('topic.detail', topic_id=topic.id))
  
  new_title = request.form.get('title')

  if new_title:
    topic.title = new_title
    topic.modify_date = datetime.now()
    db.session.commit()
    flash('토픽이 성공적으로 수정되었습니다.')
  else:
    flash('제목이 필요합니다.')
  
  return redirect(url_for('topic.detail', topic_id=topic.id))

@bp.route('/delete/<int:content_id>')
@login_required
def delete(content_id):
  content = Content.query.get_or_404(content_id)
  topic_id = content.topic_id
  if g.user.id != 1:
    flash('삭제권한이 없습니다')
    return redirect(url_for('topic.detail', topic_id=topic_id))
  db.session.delete(content)
  db.session.commit()
  return redirect(url_for('topic.detail', topic_id=topic_id))

@bp.route('/deleteTopic/<int:topic_id>')
@login_required
def deleteTopic(topic_id):
  topic = Topic.query.get_or_404(topic_id)
  # topic_id = content.topic_id
  if g.user.id != 1:
    flash('삭제권한이 없습니다')
    return redirect(url_for('topic.detail', topic_id=topic_id))
  db.session.delete(topic)
  db.session.commit()
  return redirect(url_for('topic._list'))