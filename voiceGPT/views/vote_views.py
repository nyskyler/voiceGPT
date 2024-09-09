from flask import Blueprint, url_for, flash, g, request
from werkzeug.utils import redirect

from voiceGPT import db
from voiceGPT.models import Content, Evaluation
from voiceGPT.views.auth_views import login_required

bp = Blueprint('vote', __name__, url_prefix='/vote')

@bp.route('/content/<int:content_id>')
@login_required
def content(content_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  _content = Content.query.get_or_404(content_id)
  if g.user in _content.voter:
    flash("이 질문은 이미 '좋아요'를 누르셨습니다.")
    if _content.topic_id:
      return redirect(url_for('topic.detail', topic_id=_content.topic_id, kw=kw, so='registration' if so == 'recent' else so))
  else:
    _content.voter.append(g.user)
    db.session.commit()
  if _content.topic_id:
    return redirect('{}#content_{}'.format(
      url_for('topic.detail', topic_id=_content.topic_id), content_id
    ))
  else:
    return redirect(url_for('content.detail', content_id=content_id, page=page, kw=kw, so=so))

@bp.route('/evaluation/<int:eval_id>')
@login_required
def evaluation(eval_id):
  page = request.args.get('page', type=int, default=1)
  kw = request.args.get('kw', type=str, default='')
  so = request.args.get('so', type=str, default='recent')

  _evaluation = Evaluation.query.get_or_404(eval_id)
  if g.user == _evaluation.user:
    flash("본인이 작성한 글은 '좋아요'를 누를 수 없습니다.")
    return redirect(url_for('content.detail', content_id=_evaluation.content.id, page=page, kw=kw, so=so))
  elif g.user in _evaluation.voter:
    flash('이 평가는 이미 추천하셨습니다.')
    return redirect(url_for('content.detail', content_id=_evaluation.content.id, page=page, kw=kw, so=so))
  else:
    _evaluation.voter.append(g.user)
    db.session.commit()
    return redirect('{}#evaluation_{}'.format(url_for('content.detail', content_id=_evaluation.content.id, page=page, kw=kw, so=so), eval_id))

