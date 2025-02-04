from flask import Blueprint, jsonify, url_for, render_template, flash, request, g, current_app, send_from_directory, abort, send_file, Response
from werkzeug.utils import redirect
import os
import shutil
import imghdr
import base64
import datetime
import configparser
import stat
import sys
import unicodedata
import mimetypes
import time
from .auth_views import login_required
from pathlib import Path
from pdf2image import convert_from_path
from io import BytesIO
from PIL import Image, UnidentifiedImageError, ExifTags
from pymediainfo import MediaInfo
from moviepy.editor import VideoFileClip
from urllib.parse import quote

config = configparser.ConfigParser()
config.read('cloudstorage.ini')
authorized_users = config['USER']['MEMBER']
user_list = [user.strip() for user in authorized_users.split(',') if user.strip()]

bp = Blueprint('cloudstorage', __name__, url_prefix='/cloudstorage')
root_dir = Path('/Volumes/X31')

def listSubdirectoryPaths(path):
  path = Path(path)  # Ensure path is a Path object
  subdirectories = set()
  initial_subdirectories = [
    str(path / p) for p in os.listdir(path)
    if not p.startswith(('.', '$'))
    and p != 'System Volume Information'
    and (path / p).is_dir()
  ]

  for sub in initial_subdirectories:
    subdirectories.add(sub)
    subdirectories.update(listSubdirectoryPaths(sub))
    
  return list(subdirectories)

def encodeImageToBase64(file_path, img_size=150, backGroundColor=False):
  # 이미지가 올바르게 열리는지 확인
  if imghdr.what(file_path) in ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'tiff', 'webp', 'heic']:
    try:
      with Image.open(file_path) as img:
        #EXIF 데이터를 사용하여 이미지 회전
        try:
          for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == 'Orientation':
                break
          exif = img._getexif()
          if exif is not None:
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
        img_buffer = BytesIO()
        if backGroundColor:
          max_size = (img_size, img_size)
          img.thumbnail(max_size, Image.LANCZOS)
          img_out = Image.new('RGBA', (img_size, img_size), backGroundColor)
          img_out.paste(img, ((img_size - img.width) // 2, (img_size - img.height) // 2))
          img_out.save(img_buffer, format="PNG")  # PNG 또는 원하는 포맷
        else:
          img.thumbnail((img_size, img_size))
          img.save(img_buffer, format='PNG')
          
        img_buffer.seek(0)
        # 이미지를 Base64로 인코딩하여 저장
        thumbnail_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
        return thumbnail_base64   
    except UnidentifiedImageError as e:
      print(f"Error processing {file_path}: {e}")
  else:
      print(f"File {file_path} is not a valid image.")

def encodeVideoToBase64(file_path, img_size, backgroundColor):
  """
  동영상 파일 경로를 입력받아 썸네일 이미지를 생성한 뒤 Base64로 인코딩하여 반환합니다.

  :param file_path: 동영상 파일 경로
  :param img_size: 썸네일 크기 (너비와 높이 최대값)
  :return: Base64로 인코딩된 썸네일 이미지
  """
  try:
    # 동영상을 열고 첫 번째 프레임을 추출
    with VideoFileClip(file_path) as video:
      frame = video.get_frame(0)  # 첫 번째 프레임 (0초)
      img = Image.fromarray(frame)  # Numpy 배열을 PIL 이미지로 변환
      
      # 썸네일 생성
      # img.thumbnail((img_size, img_size))
      max_size = (img_size, img_size)
      img.thumbnail(max_size, Image.LANCZOS)
      img_out = Image.new('RGBA', (img_size, img_size), backgroundColor)
      img_out.paste(img, ((img_size - img.width) // 2, (img_size - img.height) // 2))
      # img_out.save(img_buffer, format="PNG")  # PNG 또는 원하는 포맷
      
      # BytesIO 객체에 이미지 저장
      output = BytesIO()
      img_out.save(output, format='PNG')  # PNG 형식으로 저장
      output.seek(0)
      
      # Base64로 인코딩
      thumbnail_base64 = base64.b64encode(output.getvalue()).decode('utf-8')
      return thumbnail_base64

  except Exception as e:
    print(f"Error processing video file {file_path}: {e}")
    return None

def convert_size(size_bytes):
  """바이트 크기를 사람이 읽을 수 있는 형식으로 변환"""
  if size_bytes == 0:
      return "0B"
  size_units = ["B", "KB", "MB", "GB", "TB"]
  unit_index = 0
  while size_bytes >= 1024 and unit_index < len(size_units) - 1:
      size_bytes /= 1024
      unit_index += 1
  return f"{size_bytes:.2f} {size_units[unit_index]}"

def get_folder_info(folder_path):
  current_path = str(folder_path)
  current_path = current_path.split('/Volumes/X31')[1]
  if not os.path.exists(folder_path):
    return {"error": "The specified folder does not exist."}
  
  if not os.path.isdir(folder_path):
    return {"error": "The specified path is not a folder."}

  # 초기 변수 설정
  total_files = 0
  total_folders = 0
  total_size = 0
  latest_modification_time = os.path.getmtime(folder_path)  # 초기값은 폴더의 수정 시간

  # 폴더 내 파일 및 하위 폴더를 순회
  for root, dirs, files in os.walk(folder_path):
    total_folders += len(dirs)
    total_files += len(files)
    for file in files:
      file_path = os.path.join(root, file)
      total_size += os.path.getsize(file_path)
      latest_modification_time = max(latest_modification_time, os.path.getmtime(file_path))

    for folder in dirs:
      folder_path = os.path.join(root, folder)
      latest_modification_time = max(latest_modification_time, os.path.getmtime(folder_path))

  # 폴더 생성 및 수정 날짜 가져오기
  creation_time = os.path.getctime(folder_path)

  # 결과 반환
  return {
    "위치": os.path.abspath(current_path),
    "파일": total_files,
    "폴더": total_folders,
    "크기": convert_size(total_size),
    "올린 날짜": datetime.datetime.fromtimestamp(creation_time).strftime("%Y-%m-%d %H:%M:%S"),
    "수정한 날짜": datetime.datetime.fromtimestamp(latest_modification_time).strftime("%Y-%m-%d %H:%M:%S"),
  }

def get_file_info(file_path):
  if not os.path.exists(file_path):
    return {"error": "The specified file does not exist."}
  
  if not os.path.isfile(file_path):
    return {"error": "The specified path is not a file."}
  
  # 파일 정보 가져오기
  current_path = str(file_path)
  current_path = current_path.split('/Volumes/X31')[1]
  file_size = os.path.getsize(file_path)  # 파일 크기
  creation_time = os.path.getctime(file_path)  # 파일 생성 시간
  modification_time = os.path.getmtime(file_path)  # 마지막 수정 시간
  
  # 사람이 읽을 수 있는 형식으로 변환
  creation_date = datetime.datetime.fromtimestamp(creation_time).strftime("%Y-%m-%d %H:%M:%S")
  modification_date = datetime.datetime.fromtimestamp(modification_time).strftime("%Y-%m-%d %H:%M:%S")
  
  # 결과 반환
  return {
    "위치": '/'.join(current_path.split('/')[:-1]),
    "크기": convert_size(file_size),
    "올린 날짜": creation_date,
    "수정한 날짜": modification_date,
  }

def get_video_metadata(file_path):
  current_path = str(file_path)
  current_path = current_path.split('/Volumes/X31')[1]

  if not os.path.exists(file_path):
    return {"error": "The specified file does not exist."}
  
  if not os.path.isfile(file_path):
    return {"error": "The specified path is not a file."}

  # 공통 정보
  file_size = os.path.getsize(file_path)
  creation_time = os.path.getctime(file_path)
  modification_time = os.path.getmtime(file_path)
  creation_date = datetime.datetime.fromtimestamp(creation_time).strftime("%Y-%m-%d %H:%M:%S")
  modification_date = datetime.datetime.fromtimestamp(modification_time).strftime("%Y-%m-%d %H:%M:%S")
  current_path = str(file_path)
  current_path = current_path.split('/Volumes/X31')[1]

  video_info = {
    "위치": '/'.join(current_path.split('/')[:-1]),
    "크기": convert_size(file_size),
    "올린 날짜": creation_date,
    "수정한 날짜": modification_date,
  }

  try:
    # MediaInfo를 통해 메타데이터 분석
    media_info = MediaInfo.parse(file_path)
    for track in media_info.tracks:
      if track.track_type == "General":
        # Encoded_Date, Tagged_Date 등의 정보 추출
        if track.encoded_date:
          video_info["촬영 일시"] = parse_mediainfo_date(track.encoded_date)
        elif track.tagged_date:
          video_info["촬영 일시"] = parse_mediainfo_date(track.tagged_date)
      elif track.track_type == "Video":
        video_info["해상도"] = f"{track.width}x{track.height}"
        video_info["길이"] = track.duration / 1000 if track.duration else None  # 초 단위로 변환
  except Exception as e:
    video_info["error"] = f"Error parsing media file: {e}"
  
  return video_info

def parse_mediainfo_date(date_str):
  """
  MediaInfo에서 제공하는 날짜 문자열을 파싱합니다.
  예: UTC 2025-01-24 14:30:00 -> 2025-01-24 14:30:00
  """
  try:
    if date_str.startswith("UTC"):
      date_str = date_str.replace("UTC ", "")
    return datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
  except ValueError:
    return date_str  # 원본 문자열을 반환 (알 수 없는 형식인 경우)

def calculate_directory_size(directory):
  """디렉토리의 총 크기를 계산"""
  total_size = 0
  for root, dirs, files in os.walk(directory):
    for file in files:
      file_path = os.path.join(root, file)
      try:
        total_size += os.path.getsize(file_path)
      except FileNotFoundError:
        # 파일이 삭제되었거나 접근할 수 없는 경우를 처리
        continue
  return total_size

@bp.route("/main/")
@login_required
def main():
  if g.user.username not in user_list:
    flash('클라우드 저장소는 인가받은 사용자만 이용가능합니다. 관리자에게 문의하세요.')
    return redirect(url_for('main.index'))
  current_loc = f"{str(root_dir)}/"
  root_list = [ current_loc + p  for p in os.listdir(root_dir) if not p.startswith(('.', '$')) and p != 'System Volume Information']
#   return jsonify({"message": root_list}), 200
  return render_template('cloudStorage/cloudStorage.html')

@bp.route("/directoryContents/", methods=['GET'])
@login_required
def listDirectoryContents():
  try:
    current_loc = root_dir
    # 보안을 위해 경로가 root_dir 내에 있는지 확인
    if not current_loc.exists() or not current_loc.is_dir():
        return jsonify({"message": "Directory not found"}), 404
    
    directory_tree = listSubdirectoryPaths(current_loc)
    directory_tree.sort()
    # root_dir 경로를 제거하고 상대 경로로 변환
    directory_tree = [str(Path(dir).relative_to(root_dir)) for dir in directory_tree]
    #directory_tree = [dir.split(str(root_dir), 1)[-1] for dir in directory_tree]

    # print(directory_tree)
    return jsonify({"message": directory_tree}), 200
  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500

@bp.route("/listDirectoryDetails/", defaults={'dir_path': ''}, methods=['GET'])
@bp.route("/listDirectoryDetails/<path:dir_path>", methods=['GET'])
@login_required
def listDirectoryDetails(dir_path):
  try:
    current_loc = root_dir / dir_path if dir_path != '' else root_dir
    if not current_loc.exists():
      return jsonify({"message": "Directory not found"}), 404
    
    directory_info = []

    # 해당 디렉토리 내의 파일 목록을 가져옵니다.
    with os.scandir(current_loc) as entries:
      for entry in entries:
        file_path = os.path.join(current_loc, entry)
        name, ext = os.path.splitext(entry)
        name = name.split('/')[-1]
        ext = ext.lower()
        image = None

        if name.startswith(('.', '$')) or name == 'System Volume Information':
          continue

        # 디렉토리인지 파일인지 판별
        if entry.is_dir():
          type_ = 'Directory'
          size_bytes = calculate_directory_size(file_path)  # 디렉토리 크기 계산
        else:
          type_ = ext
          size_bytes = entry.stat().st_size

        modification_time = datetime.datetime.fromtimestamp(entry.stat().st_mtime)
        hour = modification_time.hour
        period = "오전" if hour < 12 else "오후"
        hour_12 = hour if 1 <= hour <= 12 else (hour - 12 if hour > 12 else 12)
        modified_time_str = modification_time.strftime(f"%Y. %m. %d. {period} {hour_12}:%M")
        directory_info.append({
          "_name": name + ext,
          "_type": type_,
          "_size": size_bytes,
          "_modified": modified_time_str,
        })

        if ext in ('.png', '.jpeg', '.jpg', '.gif', '.bmp', '.tiff', '.webp', '.heic'):
          try:
            thumbnail_base64 = encodeImageToBase64(file_path, 146, (255, 255, 255, 0))
            directory_info[-1]['_thumbnail'] = thumbnail_base64
          except Exception as e:
            print(f"Error processing {entry}: {e}")
            continue
        elif ext in ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv'):
          try:
            thumbnail_base64 = encodeVideoToBase64(file_path, 146, (255, 255, 255, 0))
            directory_info[-1]['_thumbnail'] = thumbnail_base64
          except Exception as e:
            pass
        else:
          directory_info[-1]['_thumbnail'] = ''

    return jsonify({"message": directory_info}), 200

  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500

@bp.route("/getDriveUsage/", methods=['GET'])
@login_required  
def get_drive_usage():
  try:
    total, used, free = shutil.disk_usage(root_dir)
    # Convert bytes to GB
    total_gb = total / (1024 ** 3)
    used_gb = used / (1024 ** 3)
    free_gb = free / (1024 ** 3)

    driveInfo = {
      'total_gb': total_gb,
      'used_gb': used_gb,
      'free_gb': free_gb
    }
    return jsonify({"message": driveInfo}), 200
  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500
  
@bp.route("/getThumbnailAndDetails//", defaults={'item_path': ''}, methods=['GET'])
@bp.route("/getThumbnailAndDetails/<path:item_path>", methods=['GET'])
@login_required
def getThumbnailAndDetails(item_path):
  try:
    print('item_path:', item_path)
    # target_loc = root_dir / item_path if item_path != '' else root_dir
    target_loc = root_dir / item_path
    print('target_loc:', target_loc)
    if not target_loc.exists():
      return jsonify({"message": "Item not found"}), 404
    
    if os.path.isdir(target_loc):
      return jsonify({"type": "folder", "info": get_folder_info(target_loc)}), 200
    else:
      name, ext = os.path.splitext(target_loc)
      ext = ext.lower()
      if ext == '.pdf':
        images = convert_from_path(target_loc, first_page=1, last_page=1)
        image = images[0]
        
        # BytesIO 객체에 이미지 저장
        output = BytesIO()
        image.save(output, format='PNG')  # PNG 형식으로 저장
        output.seek(0)
      
        # Base64로 인코딩
        thumbnail_base64 = base64.b64encode(output.getvalue()).decode('utf-8')
        return jsonify({"type": "pdf", "info": get_file_info(target_loc), "data": thumbnail_base64}), 200
      elif ext in ['.png', '.jpeg', '.jpg', '.gif', '.bmp', '.tiff', '.webp', '.heic']:
        # 이미지 파일인 경우
        image_info = get_file_info(target_loc)
        with Image.open(target_loc) as image:
          width, height = image.size
          image_info["해상도"] = f"{width}x{height}"
          
          # Exif 데이터를 이용해 촬영 일시 확인
          exif_data = image._getexif()
          if exif_data:
            for tag, value in exif_data.items():
              decoded_tag = ExifTags.TAGS.get(tag, tag)
              if decoded_tag == "DateTimeOriginal":
                image_info["촬영 일시"] = value
                break

          thumbnail_base64 = encodeImageToBase64(target_loc, 500, (246, 247, 250, 255))
          return jsonify({"type": "image", "info": image_info, "data": thumbnail_base64}), 200
      elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv']:
        video_info = get_video_metadata(target_loc)
        thumbnail_base64 = encodeVideoToBase64(str(target_loc), 500, (246, 247, 250, 255))
        return jsonify({"type": "video", "info": video_info, "data": thumbnail_base64}), 200
      else:
        return jsonify({"type": f"{ext.lstrip('.')}", "info": get_file_info(target_loc)}), 200
  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500
  

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

@bp.route("/sendFileResponse/<path:item_path>", methods=['GET'])
@login_required
def sendFileResponse(item_path):
  try: 
    file_path = str(root_dir / item_path)
    if not os.path.exists(file_path):
      return jsonify({"error": "The specified file does not exist."}), 404

    if os.path.isdir(file_path):
      # 고유한 ZIP 파일명 생성
      timestamp = int(time.time())
      folder_name = os.path.basename(file_path)
      zip_filename = f"{folder_name}"
      zip_folder = os.path.join(root_dir, f"temp_zips_{timestamp}")
      os.makedirs(zip_folder, exist_ok=True)

      zip_path = os.path.join(zip_folder, zip_filename)

      # 폴더를 ZIP으로 압축
      shutil.make_archive(zip_path, 'zip', file_path)
      zip_path += ".zip"  # 명시적으로 .zip 추가

      # ZIP 파일이 정상적으로 생성되었는지 확인
      if not os.path.exists(zip_path):
        return jsonify({"error": "Failed to create ZIP file."}), 500

      # ZIP 파일을 스트리밍 방식으로 전송
      response = determineSendMethod(zip_path, zip_filename)

      # 응답이 완료된 후 ZIP 삭제를 보장
      def cleanup():
        try:
          if os.path.exists(zip_path):
            os.remove(zip_path)
          if os.path.exists(zip_folder):
            os.rmdir(zip_folder)
        except Exception as e:
          print(f"Error deleting ZIP file: {e}")

      response.call_on_close(cleanup)  # ✅ 응답이 종료될 때 ZIP 삭제 실행
      return response
    else:
      return determineSendMethod(file_path, os.path.basename(item_path))

  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500
  
def normalize_path(path, form="NFC"):
  """MacOS에서 한글 파일명을 정상 처리하도록 정규화"""
  return unicodedata.normalize(form, path)

def remove_readonly(func, path, _):
  """읽기 전용 파일 삭제를 위한 권한 변경"""
  os.chmod(path, stat.S_IWRITE)
  func(path)

def should_skip_file(file_path):
  """MacOS의 AppleDouble 숨김 파일(`._`)은 삭제 대상에서 제외"""
  return os.path.basename(file_path).startswith("._")

def delete_file(file_path):
  """파일 삭제 시도 (NFD → 실패 시 NFC)"""
  if should_skip_file(file_path):
    print(f"Skipping AppleDouble file: {file_path}")
    return
  
  try:
    os.remove(normalize_path(file_path, "NFD"))
  except FileNotFoundError:
    os.remove(normalize_path(file_path, "NFC"))

def delete_directory(directory_path):
  """폴더 내 모든 파일 및 하위 폴더 삭제 후, 최상위 폴더 삭제"""
  directory_path = normalize_path(directory_path, "NFC")  # ✅ 폴더는 NFC 정규화

  # 🔹 폴더 내부의 모든 파일 삭제
  for root, _, files in os.walk(directory_path, topdown=False):
    for file_name in files:
      delete_file(os.path.join(root, file_name))

  # 🔹 삭제 대상 폴더 내 모든 하위 디렉토리 목록 저장
  all_dirs = [
    normalize_path(os.path.join(root, dir_name), "NFC")
    for root, dirs, _ in os.walk(directory_path, topdown=False)
    for dir_name in dirs
  ]

  # 🔹 하위 폴더부터 삭제
  for dir_path in all_dirs:
    try:
      shutil.rmtree(dir_path, onerror=remove_readonly)
    except FileNotFoundError:
      shutil.rmtree(normalize_path(dir_path, "NFD"), onerror=remove_readonly)

  # 🔹 최상위 폴더 삭제
  try:
    shutil.rmtree(directory_path, onerror=remove_readonly)
  except FileNotFoundError:
    shutil.rmtree(normalize_path(directory_path, "NFD"), onerror=remove_readonly)

@bp.route("/deleteResource/", methods=['POST'])
@login_required
def deleteResponse():
  data = request.get_json()
  if not data or "path" not in data:
    return jsonify({"error": "Invalid request. 'path' is required."}), 400

  resource_path = os.path.abspath(os.path.join(str(root_dir), data["path"]))

  # ✅ 존재 여부 확인
  if not os.path.exists(resource_path):
    return jsonify({"error": "The specified file or directory does not exist."}), 404

  try:
    if os.path.isfile(resource_path):
      delete_file(resource_path)
    elif os.path.isdir(resource_path):
      delete_directory(resource_path)

    return jsonify({"message": f"Successfully deleted: {data['path']}"}), 200

  except Exception as e:
    print(f"Error deleting resource: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500
  
@bp.route("/createFolderAtPath/", methods=['POST'])
@login_required
def create_folder_at_path(): 
  data = request.get_json()
  if not data or "path" not in data:
    return jsonify({"error": "Invalid request. 'path' is required."}), 400
  
  target_path = str(root_dir) + '/' + data["path"]
  try:
   os.mkdir(target_path)
   return jsonify({"message": f"Successfully created: {data['path']}"}), 200
  except Exception as e:
    print(f"Error Createing Directory: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500

@bp.route("/saveUploadedFileFromChunks/", methods=['POST'])
@login_required
def saveUploadedFileFromChunks():
  try:
    file = request.files['file']
    file_name = request.form['fileName']
    file_path = request.form['filePath']
    chunk_index = int(request.form['chunkIndex'])
    total_chunks = int(request.form['totalChunks'])

    target_path = str(root_dir) + '/' + file_path

    save_path = os.path.join(target_path, file_name + ".part")

    # Chunk 데이터를 추가하여 파일 저장
    with open(save_path, 'ab') as f:
      f.write(file.read())

    # 모든 조각이 업로드되면 최종 파일로 저장
    if chunk_index == total_chunks - 1:
      os.rename(save_path, os.path.join(target_path, file_name))
      return jsonify({"message": "Upload complete"}), 200

    return jsonify({"message": "Chunk received"}), 200
  except Exception as e:
    print(f"Error Saving File: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500

@bp.route("/saveUploadedFolderFromChunks/", methods=['POST'])
@login_required
def saveUploadedFolderFromChunks():
  try:
    file = request.files['file']
    file_name = request.form['fileName']
    file_path = request.form['filePath']
    current_path = request.form['currentPath']
    chunk_index = int(request.form['chunkIndex'])
    total_chunks = int(request.form['totalChunks'])

    sub_dir = '/'.join(file_path.split('/')[:-1])
    target_path = str(root_dir) + '/' + current_path + '/' + sub_dir + '/'
    os.makedirs(os.path.dirname(target_path), exist_ok=True)  # 폴더 생성
    
    save_path = os.path.join(os.path.dirname(target_path), file_name + ".part")

    # Chunk 데이터를 추가하여 파일 저장
    with open(save_path, 'ab') as f:
      f.write(file.read())

    # 모든 조각이 업로드되면 최종 파일로 저장
    if chunk_index == total_chunks - 1:
      final_path = os.path.join(os.path.dirname(target_path), file_name)            
      # 기존 파일이 있다면 삭제
      if os.path.exists(final_path):
        os.remove(final_path)

      os.rename(save_path, final_path)
      return jsonify({"message": "Upload complete"}), 200
    return jsonify({"message": "Chunk received"}), 200
  except Exception as e:
    print(f"Error Saving File: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500

@bp.route("/view/<path:file_path>", methods=['GET'])
@login_required
def serveMediaResource(file_path):
  try:
    target_path = str(root_dir) + '/' + file_path

    if not os.path.exists(target_path):
      return jsonify({"error": "File not found"}), 404

    # if file_path.split['.'][-1].lower() == 'pdf':
    #   return send_file(target_path, mimetype='application/pdf', as_attachment=False)
    # else:
     # MIME 타입 자동 추론
    mime_type, _ = mimetypes.guess_type(target_path)
    
    return send_file(target_path, mimetype=mime_type, as_attachment=False)
  except Exception as e:
    print(f"Error Serving File: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500