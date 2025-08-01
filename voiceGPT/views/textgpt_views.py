from flask import Blueprint, jsonify, url_for, render_template, flash, request, g, current_app, send_from_directory, abort, send_file
from werkzeug.utils import redirect
from sqlalchemy.exc import SQLAlchemyError
from openai import OpenAI
import os
import json
import configparser
import copy
import uuid
import base64
import io
import base64
from .. import db
from dotenv import load_dotenv
from .auth_views import login_required
from ..models import User, Subject, Message, RoleEnum, MsgImage
from pathlib import Path
from PIL import Image, ExifTags
from datetime import datetime, timedelta

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("chatgpt_api_key")

config = configparser.ConfigParser()
config.read('textgpt.ini')
authorized_users = config['USER']['MEMBER']
user_list = [user.strip() for user in authorized_users.split(',') if user.strip()]

client = OpenAI()
bp = Blueprint('textgpt', __name__, url_prefix='/textgpt')
timeLeft = 10

# Function to encode the image
def encode_image(image_path):
  with open(image_path, "rb") as image_file:
    return base64.b64encode(image_file.read()).decode("utf-8")

def subject_to_dict(subject):
  return {
    'id': subject.id,
    'user_id': subject.user_id,
    'title': subject.title,
    'system': subject.system,
    'model': subject.model,
    'range': subject.range,
    'resolution': subject.resolution,
    'dalle_model': subject.dalle_model,
    'number_of_images': subject.number_of_images,
    'quality_of_image': subject.quality_of_image,
    'size_of_image': subject.size_of_image,
    'style_of_image': subject.style_of_image,
    'create_date': subject.create_date.isoformat()
  }

def message_to_dict(message):
  return {
    'id': message.id,
    'subject_id': message.subject_id,
    'create_date': message.create_date.isoformat(),
    'role': message.role.value,
    'content': message.content,
    'msg_images': [img.thumbnailPath for img in message.msg_images] # thumbnailPath만 추출
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
  if not data or not all(key in data for key in ('model', 'content', 'system', 'range', 'subject_id', 'images')):
    return jsonify({'error': 'Invalid input'}), 400
  
  _messages = []
  _model = data['model']
  _content = data['content']
  _system = data['system']
  _range = int(data['range'])
  _subject_id = data['subject_id']
  _images = data['images']

  # print("model: ", _model)
  # print("content: ", _content)
  # print("system: ", _system)
  # print("range: ", _range)
  # print("subject_id: ", _subject_id)
  # print("images: ", _images)
  
  if not _images:
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
  else:
    content = []
    content.append({"type": "text", "text": _content})
    for _image in _images:
      # url에 '127.0.0.1' 또는 '172.30.1.25'가 포함돼 있다면 '121.189.157.152' 수정할 것
      # _image = _image.replace("127.0.0.1", "121.189.157.152")
      # _image = _image.replace("172.30.1.25", "121.189.157.152")
      image_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(_image.split('/t_')[1])
      # print('image: ', image_path)
      # Getting the Base64 string
      base64_image = encode_image(image_path)
      content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},})
      # content.append({"type": "image_url", "image_url": {"url": _image},})
    _messages.append({"role": "user", "content": content})
    
  try:
    # print("model: ", _model)
    # print("messages: ", _messages)
    completion = client.chat.completions.create(
      model=_model,
      messages=_messages,
    )
  except Exception as e:
    print("error: ", e)
    return jsonify({"error": str(e)}), 500
  
  response = completion.choices[0].message.content
  return json.dumps({"response": response}, ensure_ascii=False), 200

def validate_image(file_path):
  # Check if the file is a PNG
  if not file_path.lower().endswith('.png'):
    return False, "File is not a PNG image."
  
  # Check the file size (4MB = 4 * 1024 * 1024 bytes)
  file_size = os.path.getsize(file_path)
  if file_size > 4 * 1024 * 1024:
    return False, "File size exceeds 4MB."

  # Check if the image is square
  with Image.open(file_path) as img:
    width, height = img.size
    if width != height:
      return False, "Image is not square."

  return True, "Image is valid."

def check_transparency(file_path):
  try:
    with Image.open(file_path) as img:
      # 이미지를 RGB 모드로 변환. 만약 이미 RGBA 모드라면 불필요하지만 안전한 방식.
      img = img.convert("RGBA")
      datas = img.getdata()

      # 각 픽셀의 알파 값이 0인(완전히 투명한) 경우가 있는지 확인
      for item in datas:
        if item[3] < 255:  # 알파 채널의 값 확인
          return True, "Image has transparency."
      return False, "Image has no transparency."
  except Exception as e:
    print(f"An error occurred: {e}")
    return False, f"{e}"

@bp.route("/generate_image/", methods=["POST"])
@login_required
def generate_image():
  try:
    data = request.get_json()
    # print(data['images'])
    # if not data or not all(key in data for key in ('prompt')):
    #   return jsonify({'error': 'Invalid input'}), 400
  
    subjectFlag = Subject.query.filter_by(user_id=g.user.id, id=data['subject_id']).first()
    # formatted_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # file_name = f'{g.user.username}_{formatted_now}_{uuid.uuid4().hex}.png'
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    # image_path = upload_folder / file_name

    response = None
    if subjectFlag: 
      if subjectFlag.dalle_model == 'dall-e-3':
        response = client.images.generate(
          model="dall-e-3",
          prompt=data['prompt'],
          size=subjectFlag.size_of_image,
          quality=subjectFlag.quality_of_image,
          n=subjectFlag.number_of_images,
          style=subjectFlag.style_of_image,
          response_format='b64_json',
        )
      elif subjectFlag.dalle_model == 'dall-e-2':
        if not data['images']:
          response = client.images.generate(
            model="dall-e-2",
            prompt=data['prompt'],
            size=subjectFlag.size_of_image,
            n=subjectFlag.number_of_images,
            response_format='b64_json',
          )
        elif data['images'] and len(data['images']) == 2:
          file_info = []
          for img_file in data['images']:
            source_filename = img_file.split('/')[-1]
            source_filename = ''.join(source_filename.split('t_')[1:])
            file_path = upload_folder / source_filename
            if not file_path.exists():
              return jsonify({'error': 'File does not exist.'}), 400
            is_valid, _ = validate_image(str(file_path))
            if not is_valid:
              return jsonify({'error': 'File is not valid.'}), 400
            has_transparency, _ = check_transparency(str(file_path))
            file_info.append((file_path, has_transparency))
          # True의 개수
          true_count = sum(1 for _, value in file_info if value)
          # True의 인덱스 찾기
          true_indices = [index for index, (_, value) in enumerate(file_info) if value]
          if not true_count == 1:
            return jsonify({'error': 'Invalid file set for dall-e-2 API.'}), 400
          # print('edit!')
          true_index = true_indices[0]
          false_index = 1 - true_index
          # print(file_info)
          # print(true_index)
          # print(false_index)
          response = client.images.edit(
            image=open(str(file_info[false_index][0]), "rb"),
            mask=open(str(file_info[true_index][0]), "rb"),
            prompt=data['prompt'],
            n=subjectFlag.number_of_images,
            size=subjectFlag.size_of_image,
            response_format='b64_json',
          )
        elif data['images'] and len(data['images']) == 1:
          source_filename = data['images'][0].split('/')[-1]
          source_filename = ''.join(source_filename.split('t_')[1:])
          file_path = upload_folder / source_filename
          if not file_path.exists():
            return jsonify({'error': 'File does not exist.'}), 400
          
          is_valid, message = validate_image(str(file_path))
          has_transparency, message2 = check_transparency(str(file_path))

          if is_valid and not has_transparency: # 이미지 변형하기 처리
            # print('variation!')
            response = client.images.create_variation(
              image=open(str(file_path), "rb"),
              n=subjectFlag.number_of_images,
              size=subjectFlag.size_of_image,
              response_format='b64_json',
            )
          elif is_valid and has_transparency: # 이미지 편집하기 처리
            # print('edit!')
            response = client.images.edit(
              image=open(str(file_path), "rb"),
              prompt=data['prompt'],
              n=subjectFlag.number_of_images,
              size=subjectFlag.size_of_image,
              response_format='b64_json',
            )
          else:
            return jsonify({'error': f'{message}, {message2}'}), 400
    else:
      response = client.images.generate(
        model="dall-e-3",
        prompt=data['prompt'],
        size="1024x1024",
        quality="standard",
        n=1,
        style="natural",
        response_format='b64_json',
      )

    msgIds = []
    for item in response.data:
      image_b64 = item.b64_json
      image_data = base64.b64decode(image_b64)
      revised_prompt = item.revised_prompt
      formatted_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
      file_name = f'{g.user.username}_{formatted_now}_{uuid.uuid4().hex}.png'
      image_path = upload_folder / file_name

      with open(image_path, 'wb') as f:
        f.write(image_data)

      image_file = io.BytesIO(image_data)
      img = Image.open(image_file)
      img.thumbnail((256, 256))
      thumbnail_path = upload_folder / f't_{file_name}'
      img.save(thumbnail_path)

      message_image = MsgImage(
        imagePath=file_name,
        thumbnailPath=f't_{file_name}',
      )

      db.session.add(message_image)
      #db.session.flush()  # 바로 commit하지 않고 현재 세션 내에서 ID를 불러옴
      msgIds.append(f't_{file_name}')
    db.session.commit()

    return jsonify({"revised_prompt": revised_prompt, "msgIds": msgIds}), 200

    # image_b64 = response.data[0].b64_json
    # image_data = base64.b64decode(image_b64)
    # revised_prompt = response.data[0].revised_prompt

    # with open(image_path, 'wb') as f:
    #   f.write(image_data)

    # image_file = io.BytesIO(image_data)
    # img = Image.open(image_file)
    # img.thumbnail((256, 256))
    # thumbnail_path = upload_folder / f't_{file_name}'
    # img.save(thumbnail_path)

    # message_image = MsgImage(
    #   imagePath=file_name,
    #   thumbnailPath=f't_{file_name}',
    # )

    # db.session.add(message_image)
    # #db.session.flush()  # 바로 commit하지 않고 현재 세션 내에서 ID를 불러옴
    # db.session.commit()

    # msgIds = []
    # msgIds.append(f't_{file_name}')

    # return jsonify({"revised_prompt": revised_prompt, "msgIds": msgIds}), 200
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    return jsonify({'error': 'Server error', 'message': str(e)}), 500

@bp.route("/upload/", methods=["POST"])
@login_required
def upload():
  data = request.get_json()
  if not data or not all(key in data for key in ('subject', 'model', 'range', 'content', 'images')):
    return jsonify({'error': 'Invalid input'}), 400
  
  subject = data['subject']
  model = data['model']
  _range = data['range']
  contents = data['content']
  _system = data['system']
  _images = data['images']
  # print(f'_images: {_images}')

  # print("subject: ", subject)
  # print("model: ", model)
  # print("range: ", _range)
  # print("contents: ", contents)
  # print("system: ", _system)
  # print("images: ", _images)

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
        system=_system,
        resolution=512,
        dalle_model='dall-e-3',
        number_of_images=1,
        quality_of_image='standard',
        size_of_image='1024x1024',
        style_of_image='vivid',
      )
      db.session.add(_subject)
      db.session.flush()
      mySubject = _subject
    else:
      mySubject = subjectFlag
    
    msgIds = []
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
      db.session.flush()
      # print(f'Message ID: {_message.id}')
      msgIds.append(_message.id)

    for _image in _images:
      parts = _image.split('/')
      filename = parts[-1]
      # print(f'filename: {filename}')
      targetImage = MsgImage.query.filter_by(thumbnailPath=filename).first_or_404()
      # print(f'targetImage: {targetImage}')
      targetImage.message_id = int(msgIds[0])
      
    db.session.commit()
    return jsonify({'message': 'created!', 'msgIds': msgIds, 'subjectId':mySubject.id }), 201
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
  
@bp.route("/upload_generated_image/", methods=["POST"])
@login_required
def upload_generated_image():
  data = request.get_json()
  # if not data or not all(key in data for key in ('subject', 'images', 'content')):
  #   return jsonify({'error': 'Invalid input'}), 400
  
  subject = data['subject']
  _images = data['images']
  _source_images = data['source_images']
  contents = data['content']
  
  # print(f'_images: {_images}')

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
        model='gpt-4.1-mini',
        range=1,
        system='',
        resolution=512,
        dalle_model='dall-e-3',
        number_of_images=1,
        quality_of_image='standard',
        size_of_image='1024x1024',
        style_of_image='vivid',
      )
      db.session.add(_subject)
      db.session.flush()
      mySubject = _subject
    else:
      mySubject = subjectFlag
    
    msgIds = []
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
      db.session.flush()
      # print(f'Message ID: {_message.id}')
      msgIds.append(_message.id)

    if _source_images:
      for _source_image in _source_images:
        parts = _source_image.split('/')
        filename = parts[-1]
        # print(f'filename: {filename}')
        targetImage = MsgImage.query.filter_by(thumbnailPath=filename).first_or_404()
        # print(f'targetImage: {targetImage}')
        targetImage.message_id = int(msgIds[0])

    for _image in _images:
      parts = _image.split('/')
      filename = parts[-1]
      # print(f'filename: {filename}')
      targetImage = MsgImage.query.filter_by(thumbnailPath=filename).first_or_404()
      # print(f'targetImage: {targetImage}')
      targetImage.message_id = int(msgIds[1])
      
    db.session.commit()
    return jsonify({'message': 'created!', 'msgIds': msgIds, 'subjectId':mySubject.id }), 201
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
    
@bp.route("/chatlist/<string:searchWord>", methods=['GET'])
@login_required
def searchlist(searchWord):
  try:
    # 검색어가 title에 포함된 Subject를 필터링
    subjects = (Subject.query
                .filter(Subject.user_id == g.user.id, Subject.title.ilike(f"%{searchWord}%"))
                .order_by(Subject.create_date.desc())
                .all())
    if subjects:
      subjects_data = [subject_to_dict(subject) for subject in subjects]
      return jsonify({'data': subjects_data}), 200
    else:
      return '', 204
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
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

    # print(itemIds)
    for itemId in itemIds:
      if itemId:  # itemId가 None이 아닌지 확인
        message_to_delete = Message.query.filter_by(id=int(itemId)).first()
        if not message_to_delete:
            return jsonify({'error': f'Message with id {itemId} not found'}), 404
        
        if message_to_delete.msg_images: 
          msg_images = [(msg_image.imagePath, msg_image.thumbnailPath) for msg_image in message_to_delete.msg_images]
          for msg_image in msg_images:
            image_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(msg_image[0]).name
            thumbnail_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(msg_image[1]).name
            
            try:
              if image_path.exists():
                os.remove(image_path)  # 폴더에서 이미지 파일 삭제
            except OSError as e:
              print(f"Error deleting file {image_path}: {e}")
            try:
              if thumbnail_path.exists():
                os.remove(thumbnail_path)  # 폴더에서 이미지 파일 삭제
            except OSError as e:
                print(f"Error deleting file {thumbnail_path}: {e}")
        
        db.session.delete(message_to_delete)
    
    db.session.commit()
    return jsonify({'success': True, 'deleted_ids': itemIds}), 200
  
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    print(f"Unexpected error: {e}")
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
    if not data or not all(key in data for key in ('id', 'system', 'model', 'range', 'topic', 'resolution')):
      return jsonify({'error': 'Invalid input'}), 400
    
    _id = data['id']
    _system = data['system']
    _model = data['model']
    _range = int(data['range'])
    _topic = data['topic']
    _resolution = data['resolution']
    # 해당 subject 찾기
    targetSubject = Subject.query.filter_by(user_id=g.user.id, id=_id).first_or_404()
    # system 필드 업데이트
    targetSubject.system = _system
    targetSubject.model = _model
    targetSubject.range = _range
    targetSubject.title = _topic
    targetSubject.resolution = _resolution
    # 데이터베이스 커밋
    db.session.commit()
    return jsonify({'message': 'system_updated!', 'subject_id': _id}), 200
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
  
@bp.route("/update/dalle_setting/", methods=['PUT'])
@login_required
def update_dalle():
  try:
    data = request.get_json()
    # 입력 데이터 검증
    if not data or not all(key in data for key in ('id', 'dalle_model', 'number_of_images', 'quality_of_image', 'size_of_image', 'style_of_image')):
      return jsonify({'error': 'Invalid input'}), 400
    _id = data['id']
    _model = data['dalle_model']
    _number = int(data['number_of_images'])
    _quality = data['quality_of_image']
    _size = data['size_of_image']
    _style = data['style_of_image']
    # 해당 subject 찾기
    targetSubject = Subject.query.filter_by(user_id=g.user.id, id=_id).first_or_404()
    # system 필드 업데이트
    targetSubject.dalle_model = _model
    targetSubject.number_of_images = _number
    targetSubject.quality_of_image = _quality
    targetSubject.size_of_image = _size
    targetSubject.style_of_image = _style
    # 데이터베이스 커밋
    db.session.commit()
    return jsonify({'message': 'system_updated!', 'subject_id': _id}), 200
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500

def deleteImagesOutdated():
  now = datetime.now()
  ago = now - timedelta(minutes=timeLeft)
  items_to_delete = MsgImage.query.filter(MsgImage.message_id == None, MsgImage.create_date < ago).all() # 업로드만 하고 처리하지 않은 이미지가 많을 수 있으므로 first()가 아닌 all()
  
  if items_to_delete:
    for item in items_to_delete:
      image_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(item.imagePath).name
      thumbnail_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(item.thumbnailPath).name
      
      try:
        if image_path.exists():
          os.remove(image_path)  # 폴더에서 이미지 파일 삭제
      except OSError as e:
        print(f"Error deleting file {image_path}: {e}")

      try:
        if thumbnail_path.exists():
          os.remove(thumbnail_path)  # 폴더에서 이미지 파일 삭제
      except OSError as e:
          print(f"Error deleting file {thumbnail_path}: {e}")

      db.session.delete(item)
    db.session.commit()

@bp.route("/upload_image/", methods=["POST"])
@login_required
def upload_image():
  deleteImagesOutdated()
  max_files = 3
  
  # request.files에서 'images[]' 키를 통해 파일 리스트를 가져옴
  files = request.files.getlist('images[]')
  resolution = request.form.get('resolution', type=int, default=512)
  currentModel = request.form.get('currentModel')

  # 파일 수 체크
  if len(files) > max_files:
    return jsonify({'error': f'최대 {max_files}개의 파일만 업로드할 수 있습니다.'}), 400

  msgIds = []

  for file in files:
    if file and file.filename:
      # 이미지 열기
      img = Image.open(file)

      # 투명성 확인
      has_transparency, message = check_transparency(file)

      #EXIF 데이터를 사용하여 이미지 회전
      try:
        for orientation in ExifTags.TAGS.keys():
          if ExifTags.TAGS[orientation] == 'Orientation':
              break
        exif = img._getexif()
        if exif:
          orientation = exif.get(orientation, None)
          if orientation == 3:
              img = img.rotate(180, expand=True)
          elif orientation == 6:
              img = img.rotate(270, expand=True)
          elif orientation == 8:
              img = img.rotate(90, expand=True)
      except (AttributeError, KeyError, IndexError):
        # EXIF 데이터가 없는 경우 처리
        pass

      img_copied = copy.deepcopy(img)
      # 이미지 크기 조정
      max_size = (resolution, resolution)
      img.thumbnail(max_size, Image.LANCZOS)
      
      # 새로운 512x512 캔버스에 중앙 배치
      if currentModel.startswith('DALL') and has_transparency:
        img_out = Image.new('RGBA', (resolution, resolution), (255, 255, 255, 0))
        img_out.paste(img, ((resolution - img.width) // 2, (resolution - img.height) // 2))
      else:
        img_out = Image.new('RGB', (resolution, resolution), (255, 255, 255))
        img_out.paste(img, ((resolution - img.width) // 2, (resolution - img.height) // 2))

      # 파일 확장자 처리
      ext = Path(file.filename).suffix.lower()
      if ext == '.heic':
        ext = '.png'
      formatted_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
      file_name = f'{g.user.username}_{formatted_now}_{uuid.uuid4().hex}{ext}'

      upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
      image_path = upload_folder / file_name

      # 이미지 저장
      img_out.save(image_path)

      # 썸네일 생성
      img_copied.thumbnail((100, 100))
      thumbnail_path = upload_folder / f't_{file_name}'
      img_copied.save(thumbnail_path)

      message_image = MsgImage(
        imagePath=file_name,
        thumbnailPath=f't_{file_name}',
      )

      db.session.add(message_image)
      #db.session.flush()  # 바로 commit하지 않고 현재 세션 내에서 ID를 불러옴
      msgIds.append(f't_{file_name}')

  db.session.commit()
  return jsonify({'msgIds': msgIds}), 200
  # return jsonify({'status': 'success'}), 200

@bp.route("/get_thumbnail/<string:filename>", methods=["GET"])
@login_required
def get_thumbnail(filename):
  try:
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)
  except FileNotFoundError:
    abort(404)

@bp.route("/get_image/<string:filename>", methods=["GET"])
def get_image(filename):
  originalFile = filename.split('t_')[1]
  # filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], originalFile)

  # if not os.path.exists(filepath):
  #   print(f"File not found: {filepath}")
  #   abort(404)

  # return send_file(filepath, mimetype="image/jpeg", as_attachment=False)
  print("originalFile: ", originalFile)
  try:
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], originalFile)
  except FileNotFoundError:
    print("FileNotFound!")
    abort(404)

@bp.route("/deleteImage/<string:filename>", methods=["DELETE"])
@login_required
def delete_image(filename):
  item_to_delete = MsgImage.query.filter_by(thumbnailPath=filename).first()

  if item_to_delete:
    image_path = Path(current_app.config["UPLOAD_FOLDER"]) / item_to_delete.imagePath
    thumbnail_path = Path(current_app.config["UPLOAD_FOLDER"]) / item_to_delete.thumbnailPath

    try:
      if image_path.exists():
        os.remove(image_path)  # 폴더에서 이미지 파일 삭제

      if thumbnail_path.exists():
        os.remove(thumbnail_path)  # 폴더에서 썸네일 이미지 파일 삭제

      db.session.delete(item_to_delete)
      db.session.commit()
      return jsonify({'message': 'deleted!', 'msgImage': filename}), 200
    except OSError as e:
      db.session.rollback()
      print(f"Error deleting file: {e}")
      return jsonify({'error': 'File deletion error', 'message': str(e)}), 500
    except SQLAlchemyError as e:
      db.session.rollback()
      return jsonify({'error': 'Database error', 'message': str(e)}), 500
    except Exception as e:
      db.session.rollback()
      return jsonify({'error': 'Server error', 'message': str(e)}), 500
    
  return jsonify({'error': 'Image not found'}), 404 # 이미지가 존재하지 않을 경우 처리


@bp.route("/get_subject_info_by_title/<string:_title>", methods=['GET'])
@login_required
def get_subject_info_by_title(_title):
  try:
    # 검색어가 title에 포함된 Subject를 필터링
    subject = Subject.query.filter_by(title=_title).first()

    if not subject:
      return jsonify({
        "message": "해당 이름의 GPT 주제가 없습니다.",
        "info": []
      }), 200
    
    info = {
      "subject_id": subject.id,
      "system": subject.system,
      "model": subject.model,
      "range": subject.range,
    }
    
    return jsonify({
      "message": "GPT 주제 조회 완료",
      "info": info
    }), 200
    
  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    return jsonify({'error': 'Server error', 'message': str(e)}), 500