from datetime import datetime

from flask import Blueprint, url_for, request, render_template, g, flash
from werkzeug.utils import redirect

from voiceGPT import db
from voiceGPT.forms import CommentForm
from voiceGPT.models import Evaluation, Comment
from .auth_views import login_required

bp = Blueprint('comment', __name__, url_prefix='/comment')

@bp.route('/create/eval/<int:eval_id>', methods=('GET', 'POST'))
@login_required
def create_comment(eval_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  form = CommentForm()
  evaluation = Evaluation.query.get_or_404(eval_id)
  if request.method == 'POST' and form.validate_on_submit():
    comment = Comment(user=g.user, content=form.content.data, 
                      create_date=datetime.now(), eval=evaluation)
    db.session.add(comment)
    db.session.commit()
    # return redirect(url_for('content.detail', content_id=evaluation.content.id))
    return redirect('{}#comment_{}'.format(
      url_for('content.detail', content_id=evaluation.content.id, page=page, kw=kw, so=so), comment.id
    ))
  return render_template('comment/comment_form.html', form=form)

@bp.route('/modify/eval/<int:comment_id>', methods=('GET', 'POST'))
@login_required
def modify_comment(comment_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  comment = Comment.query.get_or_404(comment_id)
  content_id = comment.eval.content.id
  if g.user != comment.user:
    flash('수정권한이 없습니다')
    return redirect(url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so))
  if request.method == 'POST':
    form = CommentForm()
    if form.validate_on_submit():
      form.populate_obj(comment)
      comment.modify_date = datetime.now()
      db.session.commit()
      # return redirect(url_for('content.detail', content_id=content_id))
      return redirect('{}#comment_{}'.format(
        url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so), comment.id
      ))
  else:
    form = CommentForm(obj=comment)
  return render_template('comment/comment_form.html', form=form)

@bp.route('/delete/eval/<int:comment_id>')
@login_required
def delete_comment(comment_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  comment = Comment.query.get_or_404(comment_id)
  content_id = comment.eval.content.id
  if g.user != comment.user:
    flash('삭제권한이 없습니다')
    return redirect(url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so))
  db.session.delete(comment)
  db.session.commit()
  return redirect(url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so))
