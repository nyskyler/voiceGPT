from flask import Blueprint, jsonify, url_for, render_template, flash, request, g, current_app, send_from_directory, abort, send_file
from werkzeug.utils import redirect
from sqlalchemy.exc import SQLAlchemyError
import os
import shutil
import imghdr
import base64
import datetime
import configparser
from .auth_views import login_required
from pathlib import Path
from pdf2image import convert_from_path
from io import BytesIO
from PIL import Image, UnidentifiedImageError, ExifTags
from pymediainfo import MediaInfo
from moviepy.editor import VideoFileClip


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
  if imghdr.what(file_path) in ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'tiff', 'webp']:
    try:
      with Image.open(file_path) as img:
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
    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
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

          thumbnail_base64 = encodeImageToBase64(target_loc, 200, (246, 247, 250, 255))
          return jsonify({"type": "image", "info": image_info, "data": thumbnail_base64}), 200
      elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv']:
        video_info = get_video_metadata(target_loc)
        thumbnail_base64 = encodeVideoToBase64(str(target_loc), 200, (246, 247, 250, 255))
        return jsonify({"type": "video", "info": video_info, "data": thumbnail_base64}), 200
      else:
        return jsonify({"type": f"{ext.lstrip('.')}", "info": get_file_info(target_loc)}), 200
  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500
  