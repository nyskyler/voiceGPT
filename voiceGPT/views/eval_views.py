from datetime import datetime
from flask import Blueprint, url_for, request, render_template, g, flash
from werkzeug.utils import redirect

from .. import db
from ..forms import EvaluationForm
from ..models import Content, Evaluation
from voiceGPT.views.auth_views import login_required

bp = Blueprint('eval', __name__, url_prefix='/eval')

@bp.route('/create/<int:content_id>', methods=('POST',))
@login_required
def create(content_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  form = EvaluationForm()
  content = Content.query.get_or_404(content_id)
  if form.validate_on_submit():
    comment = request.form['comment']
    eval = Evaluation(comment=comment, create_date=datetime.now(), user=g.user)
    content.eval_set.append(eval)
    db.session.commit()
    # return redirect(url_for('content.detail', content_id=content_id))
    if content.topic_id:
      return redirect('{}#content_{}'.format(
        url_for('topic.detail', topic_id=content.topic_id, kw=kw, so='registration' if so == 'recent' else so), content.id
      ))
    else:
      page = request.args.get('page', type=int, default=1)
      return redirect('{}#evaluation_{}'.format(
        url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so), eval.id
      ))
  if content.topic_id:
    return render_template('topic/topic_detail.html', content=content, form=form)
  else:
    return render_template('content/content_detail.html', content=content, form=form)

@bp.route('/modify/<int:eval_id>', methods=('GET', 'POST'))
@login_required
def modify(eval_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  evaluation = Evaluation.query.get_or_404(eval_id)
  content = evaluation.content
  if g.user != evaluation.user:
    flash('수정권한이 없습니다')
    if content.topic_id:
      return redirect(url_for('topic.detail', topic_id=content.topic_id, kw=kw, so='registration' if so == 'recent' else so))
    else:
      return redirect(url_for('content.detail', content_id=evaluation.content.id, page=page, kw=kw, so=so))
  if request.method == "POST":
    form = EvaluationForm()
    if form.validate_on_submit():
      form.populate_obj(evaluation)
      evaluation.modify_date = datetime.now()
      db.session.commit()
      if content.topic_id:
        return redirect('{}#content_{}'.format(
          url_for('topic.detail', topic_id=content.topic_id, kw=kw, so='registration' if so == 'recent' else so), content.id
        ))
      else:
        # return redirect(url_for('content.detail', content_id=evaluation.content.id))
        return redirect('{}#evaluation_{}'.format(
          url_for('content.detail', content_id=evaluation.content.id, page=page, kw=kw, so=so), evaluation.id
        ))
  else:
    form = EvaluationForm(obj=evaluation)
  return render_template('eval/eval_form.html', eval=evaluation, form=form)

@bp.route("/delete/<int:eval_id>")
@login_required
def delete(eval_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  evaluation = Evaluation.query.get_or_404(eval_id)
  content = evaluation.content
  content_id = evaluation.content.id
  if g.user != evaluation.user:
    flash('삭제권한이 없습니다')
  else:
    db.session.delete(evaluation)
    db.session.commit()
  if content.topic_id:
    # return redirect(url_for('topic.detail', topic_id=content.topic_id))
    return redirect('{}#content_{}'.format(
      url_for('topic.detail', topic_id=content.topic_id, kw=kw, so='registration' if so == 'recent' else so), content.id
    ))
  else:
    return redirect(url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so))
