from flask import Blueprint, url_for, request, jsonify, render_template, g, flash
from .auth_views import login_required
from werkzeug.utils import redirect
from voiceGPT.forms import SettingForm
from .. import db
from ..models import Topic
from datetime import datetime
import pickle

bp = Blueprint('chatgpt', __name__, url_prefix='/chatgpt')

def get_setting_data(device_id):
  try:
    with open(f"gptSetting_{device_id}.p", 'rb') as f:
      data = pickle.load(f)
  except FileNotFoundError:
    data = {
      'ssid' : 'KT_GiGA_A5CD_2.4GEXT',
      'psk' : 'zb08gc2608',
      'max_tokens' : 250,
      'gpt_role' : '당신은 초등학생들의 질문에 대해 친절하고 알기 쉽게 잘 설명하는 초등학교 선생님입니다. 매사 친절하지만 욕설이나 비윤리적인 질문, 모욕적인 요청에 대해서는 단호하게 답변을 거부할 줄 아는 한편, 학생이 올바른 질문을 할 수 있게 지도나 훈계도 하는 모범적인 선생님입니다.',
      'temperature' : 50,
      'top_p' : 50,
      'not_upload' : False,
      'verifying_user_input' : False,
      'conversation_continuity' : False,
      'topic' : '',
      'topic_category' : '',
      'input_lang' : 'ko-KR',
      'output_lang' : 'ko-KR',  
    }
  return data

def modify_setting_data(device_id, args):
  with open(f"gptSetting_{device_id}.p", 'wb') as f:
    pickle.dump(args, f)

@bp.route('/raspberryPi/<int:device_id>')
def raspberryPi(device_id):
  data = get_setting_data(device_id)
  return jsonify(data)

@bp.route('/modify/<int:device_id>/', methods=('GET', 'POST'))
@login_required
def modify(device_id):
  setting = get_setting_data(device_id)
  if g.user.id not in {1, 3}:
    flash('수정권한이 없습니다')
    return redirect(url_for('content._list'))
  
  form = SettingForm()
  if request.method == "POST" and form.validate_on_submit():
    modify_setting_data(device_id, form.data)
    try:
      setting = get_setting_data(device_id)
      if setting.get('conversation_continuity') and not setting.get('not_upload'):
        topic = Topic(
          deviceId=device_id,
          title=form.topic.data,
          category=form.topic_category.data,
          create_date=datetime.now()
        )
        db.session.add(topic)
        db.session.commit()
      flash('voiceGPT의 설정이 수정되었습니다.')
      return redirect(url_for('content._list'))
    except Exception as e:
      db.session.rollback()
      flash(f'오류 발생: {str(e)}', 'error')
      return redirect(url_for('content._list'))
  
  form.ssid.data = setting.get('ssid')
  form.psk.data = setting.get('psk')
  form.max_tokens.data = setting.get('max_tokens')
  form.gpt_role.data = setting.get('gpt_role')
  form.temperature.data = setting.get('temperature')
  form.top_p.data = setting.get('top_p')
  form.verifying_user_input.data = setting.get('verifying_user_input')
  form.not_upload.data = setting.get('not_upload')
  form.conversation_continuity.data = setting.get('conversation_continuity')
  form.topic.data = setting.get('topic')
  form.topic_category.data = setting.get('topic_category')
  form.input_lang.data = setting.get('input_lang')
  form.output_lang.data = setting.get('output_lang')
  
  return render_template('chatgpt/setup.html', form=form, device_id=device_id)