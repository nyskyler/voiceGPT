from flask import Blueprint, jsonify, url_for, render_template, flash, request, g
from werkzeug.utils import redirect
from sqlalchemy.exc import SQLAlchemyError
from openai import OpenAI
import os
import json
import configparser
from .. import db
from dotenv import load_dotenv
from .auth_views import login_required
from ..models import User, Subject, Message, RoleEnum

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("chatgpt_api_key")

config = configparser.ConfigParser()
config.read('textgpt.ini')
authorized_users = config['USER']['MEMBER']
user_list = [user.strip() for user in authorized_users.split(',') if user.strip()]

client = OpenAI()
bp = Blueprint('textgpt', __name__, url_prefix='/textgpt')

def subject_to_dict(subject):
  return {
    'id': subject.id,
    'user_id': subject.user_id,
    'title': subject.title,
    'system': subject.system,
    'model': subject.model,
    'range': subject.range,
    'create_date': subject.create_date.isoformat()
  }

def message_to_dict(message):
  return {
    'id': message.id,
    'subject_id': message.subject_id,
    'create_date': message.create_date.isoformat(),
    'role': message.role.value,
    'content': message.content,
  }

@bp.route("/main/")
@login_required
def test():
  if g.user.username not in user_list:
    flash('textGPT는 인가받은 사용자만 이용가능합니다. 관리자에게 문의하세요.')
    return redirect(url_for('main.index'))
  return render_template('textgpt/textgpt.html')

@bp.route("/question/", methods=["POST"])
@login_required
def question():
  data = request.get_json()
  if not data or not all(key in data for key in ('model', 'content', 'system', 'range', 'subject_id')):
    return jsonify({'error': 'Invalid input'}), 400
  
  _messages = []
  _model = data['model']
  _content = data['content']
  _system = data['system']
  _range = int(data['range'])
  _subject_id = data['subject_id']
  # print(_subject_id)
  
  # data['system']이 빈 문자열이 아니라면 _messages 배열에 객체로 맨 앞에 추가해야 함.
  if _system :
    _messages.insert(0, { "role": "system", "content": _system })

  # _range의 숫자의 2배수에 해당하는 아래의 형식과 같은 객체를 전달해야 함.
  if _subject_id != 'null':
    previous_records = [None] * (_range * 2)  # 초기화 시 None 사용
    sub_contents = Message.query.filter_by(subject_id=_subject_id).order_by(Message.create_date.desc()).limit(_range*2).all() 
    # message_to_dict 함수로 변환 후 리스트를 reverse로 역순 처리
    sub_content_dicts = [message_to_dict(item) for item in sub_contents]
    sub_content_dicts.reverse()  # 역순으로 리스트를 뒤집음
    for idx, obj in enumerate(sub_content_dicts, start=1):
      if idx%2 == 1:
        previous_records[idx] = obj
      else:
        previous_records[idx-2] = obj
    result = []
    cleaned_list = [item for item in previous_records if item is not None]
    for record in cleaned_list:
      result.append(dict([('role', record['role']), ('content', record['content'])]))
    _messages.extend(result)
  
  _messages.append({ "role": "user", "content": _content})

  completion = client.chat.completions.create(
    model=_model,
    messages=_messages,
  )
  response = completion.choices[0].message.content
  return json.dumps({"response": response}, ensure_ascii=False), 200

@bp.route("/upload/", methods=["POST"])
@login_required
def upload():
  data = request.get_json()
  if not data or not all(key in data for key in ('subject', 'model', 'range', 'content')):
    return jsonify({'error': 'Invalid input'}), 400
  
  subject = data['subject']
  model = data['model']
  _range = data['range']
  contents = data['content']
  _system = data['system']

  if not isinstance(contents, list):
    return jsonify({'erorr': 'Content must be a list'}), 400
  
  for _content in contents:
    if 'author' not in _content or 'desc' not in _content:
      return jsonify({'error': 'Each content must have author and desc'}), 400

  subjectFlag = Subject.query.filter_by(user_id=g.user.id, title=subject).first()
  try:
    if subjectFlag is None:
      _subject = Subject(
        user_id=g.user.id,
        title=subject,
        model=model,
        range=_range,
        system=_system
      )
      db.session.add(_subject)
      db.session.commit()
      mySubject = _subject
    else:
      mySubject = subjectFlag
    
    for _content in contents:
      try:
        role = RoleEnum(_content['author'])
      except ValueError:
        return jsonify({'error': f"Invalid role: {_content['author']}"}), 400

      _message = Message(
        subject_id=mySubject.id,
        role=role,
        content=_content['desc'],
      )
      db.session.add(_message)

    db.session.commit()
    return jsonify({'message': 'created!', 'subject_id': mySubject.id}), 201
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    return jsonify({'error': 'Server error', 'message': str(e)}), 500


@bp.route("/chatlist/", methods=['GET'])
@login_required
def chatlist():
  try:
    subjects = Subject.query.filter_by(user_id=g.user.id).order_by(Subject.create_date.desc()).all()
    if subjects:
      subjects_data = [subject_to_dict(subject) for subject in subjects]
      return jsonify({'data': subjects_data}), 200
    else:
      return '', 204
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
  
@bp.route("/content/<string:itemId>", methods=['GET'])
@login_required
def content(itemId):
  try:
    messages = Message.query.filter_by(subject_id=itemId).order_by(Message.id).all()
    if messages:
      messages_data = [message_to_dict(message) for message in messages]
      return jsonify({'data': messages_data}), 200
    else:
      return '', 204
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500

@bp.route("/delete/message/", methods=['DELETE'])
@login_required
def delete_messages():
  try:
    data = request.get_json()
    if not data or not all(key in data for key in ('first_id', 'second_id')):
      return jsonify({'error': 'Invalid input'}), 400
    
    first_id = data.get('first_id')
    second_id = data.get('second_id')
    if not first_id:
      return jsonify({'error': 'first_id is required'}), 400
    itemIds = [first_id]
    if second_id:
      itemIds.append(second_id)

    # 트랜잭션 시작
    with db.session.begin_nested():
      for itemId in itemIds:
        if itemId:  # itemId가 None이 아닌지 확인
          message_to_delete = Message.query.filter_by(id=int(itemId)).first()
          if not message_to_delete:
              return jsonify({'error': f'Message with id {itemId} not found'}), 404
          db.session.delete(message_to_delete)
    db.session.commit()
    return jsonify({'success': True, 'deleted_ids': itemIds}), 200
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    return jsonify({'error': 'Server error', 'message': str(e)}), 500

@bp.route("/delete/<string:itemId>", methods=['DELETE'])
@login_required
def delete(itemId):
  try:
    subject_to_delete = Subject.query.filter_by(user_id=g.user.id, id=itemId).first_or_404()
    delId = subject_to_delete.id
    db.session.delete(subject_to_delete)
    db.session.commit()  
    return jsonify({'message': 'deleted!', 'subject_id': delId}), 200
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
  
@bp.route("/update/system/", methods=['PUT'])
@login_required
def update():
  try:
    data = request.get_json()
    # 입력 데이터 검증
    if not data or not all(key in data for key in ('id', 'system', 'model', 'range', 'topic')):
      return jsonify({'error': 'Invalid input'}), 400
    _id = data['id']
    _system = data['system']
    _model = data['model']
    _model = 'gpt-4o' if _model == 'GPT-4o' else 'gpt-4o-mini'
    _range = int(data['range'])
    _topic = data['topic']
    # 해당 subject 찾기
    targetSubject = Subject.query.filter_by(user_id=g.user.id, id=_id).first_or_404()
    # system 필드 업데이트
    targetSubject.system = _system
    targetSubject.model = _model
    targetSubject.range = _range
    targetSubject.title = _topic
    # 데이터베이스 커밋
    db.session.commit()
    return jsonify({'message': 'system_updated!', 'subject_id': _id}), 200
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500