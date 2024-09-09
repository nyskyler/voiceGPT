from flask import Blueprint, render_template, url_for
from werkzeug.utils import redirect

from voiceGPT.models import Content

bp = Blueprint('main', __name__, url_prefix='/')

@bp.route('/')
def index():
  return redirect(url_for('content._list'))

