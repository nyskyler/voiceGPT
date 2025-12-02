from flask import Blueprint, jsonify, url_for, render_template, flash, request, g, current_app, send_from_directory, abort, send_file, Response
from werkzeug.utils import redirect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from openai import OpenAI
from typing import Optional, Dict, Any
import json
import ast
import os
import configparser
import copy
import uuid
import base64
import io
import re
import time
import traceback
import mimetypes
from .. import db
from dotenv import load_dotenv
from .auth_views import login_required
from ..models import User, Subject, Message, RoleEnum, MsgImage, MsgFile
from pathlib import Path
from PIL import Image, ExifTags
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote, quote

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("chatgpt_api_key")

config = configparser.ConfigParser()
config.read('textgpt.ini')
authorized_users = config['USER']['MEMBER']
user_list = [user.strip() for user in authorized_users.split(',') if user.strip()]

client = OpenAI()
root_dir = Path('/Volumes/X31')
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
    'msg_images': [img.thumbnailPath for img in message.msg_images], # thumbnailPath만 추출
    'msg_files': [(file.id, file.filename) for file in message.msg_files] #id와 filename 추출
  }

@bp.route("/main/")
@login_required
def test():
  if g.user.username not in user_list:
    flash('ChatGPT는 인가받은 사용자만 이용가능합니다. 관리자에게 문의하세요.')
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


def extract_after_view(url: str) -> str:
  """
  URL의 path에서 'view' 세그먼트 다음 경로를 반환.
  예: http://.../cloudstorage/view/obsidian/voiceGPT.pdf -> 'obsidian/voiceGPT.pdf'
  'view'가 없으면 '' 반환.
  """
  parsed = urlparse(url)
  # URL-인코딩 경로 복원
  path = unquote(parsed.path)
  segments = [seg for seg in path.split('/') if seg]

  try:
    idx = segments.index('view')
  except ValueError:
    return ''
  return '/'.join(segments[idx + 1:])


def _secure_join_under_root(root: Path, relative_unix_path: str) -> Path:
  """
  relative_unix_path를 root 아래 안전하게 결합한 절대 경로를 반환.
  루트 밖으로 벗어나면 ValueError 발생.
  """
  root_resolved = root.resolve()
  # URL에서 온 경로는 POSIX 기준이므로 슬래시로 나뉜 것을 그대로 사용
  candidate = (root_resolved / Path(relative_unix_path)).resolve()
  try:
    candidate.relative_to(root_resolved)
  except ValueError:
    # 루트 밖으로 벗어난 경우
    raise ValueError("Path traversal detected")
  return candidate


@bp.route("/pdf_file_input/", methods=["POST"])
@login_required
def pdf_file_input():
  req_json = request.get_json(silent=True)

  if not req_json or not all(k in req_json for k in ('model', 'content')):
    return jsonify({'error': 'Invalid input', 'message': "Required keys: 'model', 'content'"}), 400

  _model = req_json.get('model')
  _content = req_json.get('content')

  if not isinstance(_model, str) or not _model.strip():
    return jsonify({'error': 'Invalid input', 'message': "'model' must be a non-empty string"}), 400
  if not isinstance(_content, str) or not _content.strip():
      return jsonify({'error': 'Invalid input', 'message': "'content' must be a non-empty string"}), 400

  _URL_TAG_PATTERN = re.compile(r'>>\s*(https?://[^\s<>]+)\s*<<', re.IGNORECASE)

  # 1) URL 추출
  urls = _URL_TAG_PATTERN.findall(_content)

  # 2) URL 패턴 제거한 본문 텍스트
  text = _URL_TAG_PATTERN.sub('', _content).strip()

  # 3) URL -&gt; 파일 경로 변환
  #    - /view/ 뒤의 경로만 사용
  #    - root_dir 아래로 안전하게 결합
  #    - 중복 제거(순서 보존)
  seen = set()
  rel_paths = []
  errors = []
  for url in urls:
    rel = extract_after_view(url)
    if not rel:
      errors.append({'url': url, 'error': "URL path doesn't contain '/view/' or nothing follows it"})
      continue
    # 선행/후행 슬래시 제거
    rel = rel.strip('/')
    # 중복 제거
    if rel in seen:
      continue
    seen.add(rel)
    # 보안 결합
    try:
      candidate = _secure_join_under_root(root_dir, rel)
    except ValueError:
      errors.append({'url': url, 'error': 'Path traversal attempt or invalid path'})
      continue

    if not candidate.exists():
      errors.append({'url': url, 'error': 'File not found'})
      continue
    if not candidate.is_file():
      errors.append({'url': url, 'error': 'Path is not a file'})
      continue

    rel_paths.append(candidate)

  # 파일 관련 에러가 하나라도 있으면 상세 정보와 함께 404 반환
  # (필요에 따라 부분 성공 허용으로 바꿀 수 있음)
  if errors:
    return jsonify({'error': 'File resolution error', 'details': errors}), 404

  content_list = []
  if text:
    content_list.append({"type": "text", "text": text})

  # 4) 파일들을 base64로 인코딩해 content_list에 추가
  for file_path in rel_paths:
    try:
      file_bytes = file_path.read_bytes()
    except Exception as e:
      return jsonify({'error': 'File read error', 'file': str(file_path), 'message': str(e)}), 500

    base64_string = base64.b64encode(file_bytes).decode("utf-8")
    content_list.append({
      "type": "file",
      "file": {
        "filename": file_path.name,
        "file_data": f"data:application/pdf;base64,{base64_string}",
      }
    })

  # 5) Chat Completions 호출
  try:
    completion = client.chat.completions.create(
      model=_model,
      messages=[
        {
          "role": "user",
          "content": content_list
        },
      ],
    )
    content = completion.choices[0].message.content
    # JSON 응답
    return jsonify({"response": content}), 200
  except Exception as e:
    print(traceback.format_exc())
    return jsonify({'error': 'Server error', 'message': str(e)}), 500


### Dictionary와 Pydantic 객체를 모두 안전하게 접근하는 함수
def _get(obj, key, default=None):
  """dict와 객체를 모두 안전하게 접근"""
  if isinstance(obj, dict):
    return obj.get(key, default)
  return getattr(obj, key, default)


### response에서 container_file_citation 주석 추출하기
def extract_container_file_citations(response):
  citations = []
  seen = set()  # (file_id, filename, container_id)

  for item in _get(response, "output", []) or []:
    if _get(item, "type") != "message":
      continue

    for content in _get(item, "content", []) or []:
      for ann in _get(content, "annotations", []) or []:
        ann_type = _get(ann, "type")
        if ann_type in ("container_file_citation", "AnnotationContainerFileCitation"):
          container_id = _get(ann, "container_id")
          file_id      = _get(ann, "file_id")
          filename     = _get(ann, "filename")

          key = (file_id, filename, container_id)
          if key in seen:
            continue
          seen.add(key)

          citations.append({
            "container_id": container_id,
            "file_id": file_id,
            "filename": filename,
          })
  return citations    


@bp.route("/code_interpreter/", methods=["POST"])
@login_required
def code_interpreter():
  req_json = request.get_json(silent=True)

  # ---------- 입력 검증 ----------
  if not req_json or not all(k in req_json for k in ("model", "content", "system")):
    return jsonify(
      {"error": "Invalid input", "message": "Required keys: 'model', 'content', 'system'"}
    ), 400

  _model = req_json.get("model")
  _content = req_json.get("content")
  _system = req_json.get("system")

  if not isinstance(_model, str) or not _model.strip():
    return jsonify({"error": "Invalid input", "message": "'model' must be a non-empty string"}), 400
  if not isinstance(_content, str) or not _content.strip():
    return jsonify({"error": "Invalid input", "message": "'content' must be a non-empty string"}), 400

  # ---------- %% /path/file.txt %% 태그 파싱 ----------
  url_tag_pattern = re.compile(r"%%\s*(/.*?)\s*%%", re.UNICODE)
  urls = url_tag_pattern.findall(_content)
  text = url_tag_pattern.sub("", _content).strip()

  seen = set()
  rel_paths = []
  errors = []

  for url in urls:
    rel = url.strip("/")
    if rel in seen:
      continue
    seen.add(rel)

    try:
      candidate = _secure_join_under_root(root_dir, rel)
    except ValueError:
      errors.append({"url": url, "error": "Path traversal attempt or invalid path"})
      continue

    if not candidate.exists():
      errors.append({"url": url, "error": "File not found"})
      continue
    if not candidate.is_file():
      errors.append({"url": url, "error": "Path is not a file"})
      continue

    rel_paths.append(candidate)

  if errors:
    return jsonify({"error": "File resolution error", "details": errors}), 404

  uploaded_file_ids = []
  response = None
  container_ids = set()
  msg_files = []

  try:
    # ---------- 파일 업로드 ----------
    for p in rel_paths:
      file_obj = client.files.create(
        file=open(p, "rb"),
        purpose="user_data",
        expires_after={"anchor": "created_at", "seconds": 3600}
      )
      uploaded_file_ids.append(file_obj.id)

    # ---------- Responses API 실행 ----------
    response = client.responses.create(
      model=_model,
      tools=[{
        "type": "code_interpreter",
        "container": {"type": "auto", "file_ids": uploaded_file_ids}
      }],
      instructions=_system,
      input=text,
    )

    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    upload_folder.mkdir(parents=True, exist_ok=True)

    citations = extract_container_file_citations(response)

    for c in citations:
      # print(f'container_id: {c["container_id"]}')
      # print(f'file_id: {c["file_id"]}')
      # print(f'filename: {c["filename"]}')
      container_ids.add(c["container_id"])

      # 컨테이너에서 파일 다운로드
      result = client.containers.files.content.retrieve(
        file_id=c["file_id"],
        container_id=c["container_id"],
      )
      blob = result.read()  # bytes
      size = len(blob)

      # 파일명 중복 방지: "2025-12-03_12-33-44_XXXX-UUID_filename"
      formatted_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
      unique = uuid.uuid4().hex[:8]
      save_name = f"{formatted_now}_{unique}_{c['filename']}"
      file_path = upload_folder / save_name

      with open(file_path, "wb") as fp:
        fp.write(blob)

      # DB 기록
      msg_file = MsgFile(
        filePath=str(file_path),
        filename=save_name,
        size=size,
        mimeType=mimetypes.guess_type(c["filename"])[0] or "application/octet-stream",
      )
      db.session.add(msg_file)
      db.session.flush()

      msg_files.append({
        "id": msg_file.id,
        "filename": save_name,
      })

    db.session.commit()

  except Exception:
    db.session.rollback()
    current_app.logger.error("code_interpreter error: %s", traceback.format_exc())
    return jsonify({
      "error": "Code Interpreter execution failed",
      "details": traceback.format_exc()
    }), 500

  finally:
    # 컨테이너 삭제 시도 (실패하더라도 무시)
    if container_ids:
      for cid in container_ids:
        try:
          client.containers.delete(cid)
        except Exception:
          pass

  # ---------- 응답 반환 ----------
  output_text = getattr(response, "output_text", "") or ""
  return jsonify({
    "response": output_text,
    "msg_files": msg_files,
  }), 201


# 파일 크기에 따른 청크 크기 설정 (사이즈: 청크 크기)
CHUNK_SIZES = {
  1 * 1024 * 1024: 1024 * 64,   # 1MB 이상 100MB 미만 → 64KB
  100 * 1024 * 1024: 1024 * 128,  # 100MB 이상 500MB 이하 → 128KB
  500 * 1024 * 1024: 1024 * 512,  # 500MB 이상 1GB 미만 → 512KB
  1 * 1024 * 1024 * 1024: 1024 * 1024  # 1GB 이상 → 1MB
}

def generate_large_file(file_path, chunk_unit):
  """파일을 청크 단위로 읽어서 반환하는 제너레이터"""
  with open(file_path, "rb") as file:
    while chunk := file.read(chunk_unit):  # 8KB 단위로 읽기
      yield chunk

def send_large_file(file_path, chunk_size, filename):
  """ 큰 파일을 스트리밍으로 전송하는 함수 """
  file_size = os.path.getsize(file_path)  # 🔥 파일 크기 가져오기
  # 파일명을 URL-encoded UTF-8로 변환 (RFC 5987 표준 적용)
  encoded_filename = quote(filename, encoding='utf-8')

  return Response(
    generate_large_file(file_path, chunk_size),  # ✅ 들여쓰기 정리
    mimetype="application/octet-stream",
    headers={
      "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
      "Content-Length": str(file_size),  # ✅ 올바른 형식
    }
)

def determineSendMethod(file_path, file_name):
  file_size = os.path.getsize(file_path)
  if file_size < 1 * 1024 * 1024:  # 1MB 미만
    return send_file(file_path, as_attachment=True)
  
  # 파일 크기에 맞는 청크 크기 찾기
  chunk_size = next((size for limit, size in CHUNK_SIZES.items() if file_size < limit), 1024 * 1024)
  return send_large_file(file_path, chunk_size, file_name)


@bp.route("/serve_file_by_id/<string:fileId>", methods=["GET"])
@login_required
def serve_file_by_id(fileId):
  try:
    fid = int(fileId)
    targetFile = MsgFile.query.filter_by(id=fid).first_or_404()
    print(targetFile)
    return determineSendMethod(targetFile.filePath, targetFile.filename)
  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500


def to_dict_if_valid(s: str) -> Optional[Dict[str, Any]]:
  """문자열이 dict(JSON 또는 파이썬 리터럴) 형식이면 dict 반환."""
  if not isinstance(s, str):
    return None
  s = s.strip()

  # JSON 시도
  try:
    obj = json.loads(s)
    if isinstance(obj, dict):
      return obj
  except json.JSONDecodeError:
    pass

  # Python literal 시도
  try:
    obj = ast.literal_eval(s)
    if isinstance(obj, dict):
      return obj
  except (ValueError, SyntaxError):
    pass

  return None


def extract_and_remove(s: str, left: str = "[[", right: str = "]]", normalize: bool = False):
  """문자열에서 left~right 사이 내용을 추출하고 나머지를 반환."""
  start = s.find(left)
  if start == -1:
    return ("", s)
  end = s.find(right, start + len(left))
  if end == -1:
    return ("", s)

  extracted = s[start + len(left): end]
  remaining = s[:start] + s[end + len(right):]

  if normalize:
    remaining = re.sub(r"\s+", " ", remaining)  # 연속 공백 제거
    remaining = re.sub(r"\s+([,.;:!?~])", r"\1", remaining)  # 구두점 앞 공백 제거
    remaining = remaining.strip()

  return extracted, remaining


@bp.route("/web_search/", methods=["POST"])
@login_required
def web_search():
  try:
    req_json = request.get_json(silent=True)
    if not req_json or not all(k in req_json for k in ('model', 'content')):
      return jsonify({'error': 'Invalid input', 'message': "Required keys: 'model', 'content'"}), 400

    _model = req_json.get('model')
    _content = req_json.get('content')

    if not isinstance(_model, str) or not _model.strip():
      return jsonify({'error': 'Invalid input', 'message': "'model' must be a non-empty string"}), 400
    if not isinstance(_content, str) or not _content.strip():
      return jsonify({'error': 'Invalid input', 'message': "'content' must be a non-empty string"}), 400

    # [[...]] 내부 dict 추출
    dict_for_tools = {"type": "web_search_preview"}
    extracted, remaining = extract_and_remove(_content[2:], normalize=True)

    print("extracted: ", extracted)
    print("remaining: ", remaining)

    if extracted:
      parsed_dict = to_dict_if_valid(extracted)
      if parsed_dict:
        user_location = {}
        for key in ("country", "city", "region"):
          if key in parsed_dict:
            user_location[key] = parsed_dict[key]
        if user_location:
          user_location["type"] = "approximate"
          dict_for_tools["user_location"] = user_location

        if "search_context_size" in parsed_dict:
          val = parsed_dict["search_context_size"]
          if isinstance(val, str) and val in ("low", "medium", "high"):
            dict_for_tools["search_context_size"] = val
    
    print("dict_for_tools: ", dict_for_tools)
    
    # API 호출
    response = client.responses.create(
      model=_model.strip(),
      tools=[dict_for_tools],
      input=remaining
    )

    return jsonify({"response": response.output_text}), 200

  except Exception as e:
    current_app.logger.error("web_search error: %s", traceback.format_exc())
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
 

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


def validate_image_2(file_path):
  # Check if the file is a PNG
  if not file_path.lower().endswith('.png'):
    return False, "File is not a PNG image."
  
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
  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  except Exception as e:
    db.session.rollback()
    return jsonify({'error': 'Server error', 'message': str(e)}), 500
  

@bp.route("/generate_image_by_imageAPI/", methods=["POST"])
@login_required
def generate_image_by_imageAPI():
  try:
    data = request.get_json()
    if not data or "model" not in data or "prompt" not in data:
      return jsonify({'error': 'Invalid input'}), 400

    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    revised_prompt = None
    imagePathes = []  # [(Path, has_transparency_bool), ...]

    # ---------------------------
    # 내부 함수: 이미지 경로 검증
    # ---------------------------
    def process_image_path(path: Path):
      if not path.exists():
        return None
      is_valid, _ = validate_image_2(str(path))
      if not is_valid:
        return None
      has_transparency, _ = check_transparency(str(path))
      return (path, has_transparency)

    # ---------------------------
    # msgIds_list 처리
    # ---------------------------
    msgIds_list = data.get('msgIds', [])
    print("msgIds_list: ", msgIds_list)
    for msgId in msgIds_list:
      try:
        msgId_int = int(msgId)
      except ValueError:
        continue
      msg = (
        Message.query.options(selectinload(Message.msg_images))
        .filter_by(id=msgId_int)
        .first()
      )
      if not msg or not msg.msg_images:
        continue
      img_path = upload_folder / msg.msg_images[0].imagePath
      result = process_image_path(img_path)
      if result:
        imagePathes.append(result)

    # ---------------------------
    # images_list 처리
    # ---------------------------
    images_list = data.get('images', [])
    for img_file in images_list:
      source_filename = img_file.split('/')[-1]
      source_filename = ''.join(source_filename.split('t_')[1:])
      img_path = upload_folder / source_filename
      result = process_image_path(img_path)
      if result:
        imagePathes.append(result)

    # ---------------------------
    # API 호출
    # ---------------------------
    num_of_images = len(imagePathes)

    if num_of_images == 0:
      # 새 이미지 생성
      response = client.images.generate(
        model="gpt-image-1",
        prompt=data['prompt']
      )

    elif num_of_images == 1:
      # 단일 이미지 편집
      with open(str(imagePathes[0][0]), "rb") as img_file:
        response = client.images.edit(
            model="gpt-image-1",
            image=img_file,
            prompt=data['prompt']
        )

    else:
      # 다중 이미지 처리
      true_count = sum(flag for _, flag in imagePathes)
      true_indices = [i for i, (_, flag) in enumerate(imagePathes) if flag]

      if true_count == 0:
        # 모든 이미지 불투명
        open_files = [open(str(f), "rb") for f, _ in imagePathes]
        try:
          response = client.images.edit(
            model="gpt-image-1",
            image=open_files,
            prompt=data['prompt']
          )
        finally:
          for f in open_files:
            f.close()

      elif true_count == 1 and num_of_images == 2:
        # 하나는 mask, 하나는 원본
        true_index = true_indices[0]
        false_index = 1 - true_index
        with open(str(imagePathes[false_index][0]), "rb") as img_file, \
              open(str(imagePathes[true_index][0]), "rb") as mask_file:
          response = client.images.edit(
            model="gpt-image-1",
            image=img_file,
            mask=mask_file,
            prompt=data['prompt'],
          )
      else:
        print("step: 4")
        return jsonify({'error': 'Invalid file set for imageAPI.'}), 400

    # ---------------------------
    # 응답 저장 & DB 기록
    # ---------------------------
    msgIds = []
    for item in response.data:
      image_b64 = item.b64_json
      image_data = base64.b64decode(image_b64)
      revised_prompt = item.revised_prompt

      formatted_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
      file_name = f'{g.user.username}_{formatted_now}_{uuid.uuid4().hex}.png'
      image_path = upload_folder / file_name

      # 원본 저장
      with open(image_path, 'wb') as f:
        f.write(image_data)

      # 썸네일 저장
      image_file = io.BytesIO(image_data)
      img = Image.open(image_file)
      img.thumbnail((256, 256))
      thumbnail_name = f't_{file_name}'
      thumbnail_path = upload_folder / thumbnail_name
      img.save(thumbnail_path)

      # DB 저장
      message_image = MsgImage(
        imagePath=file_name,
        thumbnailPath=thumbnail_name,
      )
      db.session.add(message_image)
      msgIds.append(thumbnail_name)

    db.session.commit()

    return jsonify({"revised_prompt": revised_prompt, "msgIds": msgIds}), 200

  except SQLAlchemyError as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({'error': 'Database error', 'message': str(e)}), 500

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
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
  _files = data['files']

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

    for _file in _files:
      targetFile = MsgFile.query.filter_by(id=_file["id"]).first_or_404()
      targetFile.message_id = msgIds[-1]   # 항상 최신 메시지(assistant)에 연결
      
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
      # PNG가 아닌 모든 확장자는 PNG로 저장
      if ext not in ['.png']:
        ext = '.png'

      formatted_now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
      file_name = f'{g.user.username}_{formatted_now}_{uuid.uuid4().hex}{ext}'

      upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
      image_path = upload_folder / file_name

      print("image_path: ", image_path)

      # 이미지 저장 (항상 PNG로)
      img_out.save(image_path, format="PNG")

      # 썸네일 생성 (항상 PNG로)
      img_copied.thumbnail((100, 100))
      thumbnail_path = upload_folder / f't_{file_name}'
      img_copied.save(thumbnail_path, format="PNG")

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