from flask import Blueprint, jsonify, render_template, request, abort, Response, g
import os, csv, json, time, threading, logging, re, pickle, tempfile
import configparser
from datetime import datetime
from collections import deque
from .auth_views import login_required
from dotenv import load_dotenv
from operator import itemgetter
from pathlib import Path
from pytz import timezone as tz, UTC
from typing import Any
from redis import Redis

bp = Blueprint('scienceon', __name__, url_prefix='/scienceon')
root_dir = Path('/Volumes/X31/ScienceON')

# 스트림 상태 관리
locks = {}          # stream -> threading.Lock (CSV 동시성 제어)
header_cache = {}   # stream -> [columns]

redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
logger = logging.getLogger(__name__)

config = configparser.ConfigParser()
config.read('cloudstorage.ini')
authorized_users = config['USER']['MEMBER']
user_list = [user.strip() for user in authorized_users.split(',') if user.strip()]


@bp.route("/main/", methods=['GET'])
def main():
  username = getattr(g.user, "username", None)
  if username in user_list:
      return render_template("scienceOn/scienceOn.html")
  else:
      return render_template("scienceOn/scienceOn_public.html")


def _utc_ts():
  return datetime.now(tz('Asia/Seoul'))


def _get_lock(stream):
  return locks.setdefault(stream, threading.Lock())


def _csv_path(stream, fileName):
  return os.path.join(root_dir, stream, f"{fileName}.csv")


def _ensure_header(stream, record):
  """
  CSV 파일에 올바른 헤더가 존재하는지 확인하고, 없으면 record의 key로 새로 생성한다.
  """
  path = _csv_path(stream, stream)

  # 1️⃣ 캐시 우선
  if stream in header_cache:
    return header_cache[stream]

  header = None
  if os.path.exists(path):
    try:
      with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)

        # 값처럼 보이거나 비어 있으면 무효 처리
        if not header or all(col.strip() == "" for col in header):
          header = None
        elif header and any("," in h for h in header):
          header = None
    except Exception:
      header = None

  # 2️⃣ 헤더가 없으면 record의 key를 사용하여 새로 생성
  if header is None:
    header = list(record.keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
      writer = csv.writer(f)
      writer.writerow(header)
    logger.info(f"[{stream}] 새 헤더 생성: {header}")

  # 3️⃣ 캐시 저장
  header_cache[stream] = header
  return header


def append_csv(stream, record):
  """
  record(dict)를 stream.csv에 추가한다.
  """
  lock = _get_lock(stream)
  path = _csv_path(stream, stream)
  header = _ensure_header(stream, record)

  # 헤더에 없는 키는 무시
  row = [record.get(col, "") for col in header]

  try:
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
      writer = csv.writer(f)
      writer.writerow(row)
  except Exception as e:
    logger.error(f"[{stream}] CSV append 실패: {e}")

  return header


def read_csv_as_json(stream, fileName, limit=None):
  path = _csv_path(stream, fileName)
  if not os.path.exists(path):
    return []

  lock = _get_lock(stream)
  with lock:
    items = deque(maxlen=limit or 0)
    with open(path, "r", newline="", encoding="utf-8") as f:
      reader = csv.DictReader(f)
      if limit is None:
        return list(reader)
      for row in reader:
        items.append(row)
    return list(items)


def broadcast(stream, record_dict):
  logger.info("broadcast pid=%s stream=%s", os.getpid(), stream)
  data = json.dumps(record_dict, ensure_ascii=False, default=str)
  redis.publish(f"sse:{stream}", data)


def listDirectoryTree(path):
  path = Path(path)
  all_paths = []

  for item in path.iterdir():
    if item.name.startswith(('.', '$')) or item.name in ('System Volume Information', 'settings'):
      continue
    all_paths.append(str(item))
    if item.is_dir():
      all_paths.extend(listDirectoryTree(item))
  return all_paths


def load_transmission_config(macAddr: str) -> dict:
  safe_stream = re.sub(r'[^A-Fa-f0-9]', '', macAddr or '')
  if not safe_stream:
    raise ValueError("유효한 macAddr이 필요합니다.")

  settings_dir = os.path.join(root_dir, 'settings')
  os.makedirs(settings_dir, exist_ok=True)  # settings 디렉토리 보장
  file_path = os.path.join(settings_dir, f"{safe_stream}.p")

  # 기본값
  default_config = {
    "sendInterval": 10000,
    "appendMode": True,
    "sendLimitOn": False,
    "sendLimitCount": None,
    "useEndAt": False,
    "endAt": None,
    "chartWindow": 50,
  }

  # 파일이 없으면 생성 후 반환
  if not os.path.exists(file_path):
    try:
      fd, tmp_path = tempfile.mkstemp(prefix=f"{safe_stream}.", suffix=".tmp", dir=settings_dir)
      try:
        with os.fdopen(fd, 'wb') as f:
          pickle.dump(default_config, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, file_path)
      except Exception:
        try:
          if os.path.exists(tmp_path):
            os.remove(tmp_path)
        finally:
          raise
    except Exception as e:
      raise RuntimeError(f"기본 설정 저장 실패: {e}")
    return {**default_config, "stream": safe_stream}

  # 기존 파일이 있는 경우 로드
  with open(file_path, 'rb') as f:
    obj: Any = pickle.load(f)

  if isinstance(obj, dict):
    config = dict(obj)
  else:
    config = dict(getattr(obj, "__dict__", {}))

  def to_jsonable(v):
    if isinstance(v, datetime):
      return v.isoformat()
    return v

  return {
    "sendInterval": config.get("sendInterval"),
    "appendMode": config.get("appendMode"),
    "sendLimitOn": config.get("sendLimitOn"),
    "sendLimitCount": config.get("sendLimitCount"),
    "useEndAt": config.get("useEndAt"),
    "endAt": to_jsonable(config.get("endAt")),
    "chartWindow": config.get("chartWindow"),
    "stream": safe_stream,
  }


@bp.route("/directoryAllContents/", methods=['GET'])
def listAllContents():
  try:
    current_loc = root_dir
    if not current_loc.exists() or not current_loc.is_dir():
      return jsonify({"error": "Directory not found"}), 404

    directory_tree = listDirectoryTree(current_loc)
    directory_tree.sort()
    directory_tree = ['/' + str(Path(dir).relative_to(root_dir)) for dir in directory_tree]

    return jsonify({"paths": directory_tree}), 200
  except Exception as e:
    logger.exception("Error listing directory contents")
    return jsonify({"error": "Unexpected error"}), 500


@bp.route("/append_device_sensor_data_to_csv_and_broadcast/<stream>", methods=['POST'])
def ingest(stream):
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify(error="JSON object required"), 400

  if "ts" not in payload:
    payload["ts"] = _utc_ts().isoformat()

  target_path = os.path.join(root_dir, stream)
  if not os.path.exists(target_path):
    try:
      os.mkdir(target_path)
    except Exception as e:
      logger.exception("Error creating directory for stream %s", stream)
      return jsonify({"error": "Directory creation failed"}), 500

  # --- (1) 설정파일 로드 ---
  settings_dir = os.path.join(root_dir, 'settings')
  os.makedirs(settings_dir, exist_ok=True)
  config_path = os.path.join(settings_dir, f"{stream}.p")

  config = None
  if os.path.exists(config_path):
    try:
      with open(config_path, 'rb') as f:
        obj = pickle.load(f)
      config = dict(obj) if isinstance(obj, dict) else dict(getattr(obj, "__dict__", {}))
    except Exception as e:
      logger.warning("Failed to load config for stream=%s: %s", stream, e)

  # --- (2) appendMode 확인 ---
  append_mode = True
  if config and isinstance(config, dict):
    append_mode = bool(config.get("appendMode", True))

  if not append_mode:
    csv_path = _csv_path(stream, stream)

    # (3a) CSV가 존재할 경우 이름 변경
    if os.path.exists(csv_path):
      try:
        # CSV 마지막 행의 ts값 읽기
        last_ts_str = None
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
          reader = csv.DictReader(f)
          for row in reader:
            last_ts_str = row.get("ts", None)
        if not last_ts_str:
          last_ts_str = _utc_ts().isoformat()

        # 파일명 안전화
        safe_ts = re.sub(r'[^0-9T]', '_', last_ts_str.split("+")[0])
        new_name = f"{safe_ts}.csv"
        new_path = os.path.join(target_path, new_name)
        os.rename(csv_path, new_path)
        logger.info("Renamed CSV for %s -> %s", stream, new_name)
      except Exception as e:
        logger.exception("Failed to rename CSV for stream %s", stream)
        return jsonify({"error": f"CSV rename failed: {e}"}), 500

    # (3b) appendMode를 True로 변경 후 저장
    header_cache.pop(stream, None)
    config["appendMode"] = True
    try:
      fd, tmp_path = tempfile.mkstemp(prefix=f"{stream}.", suffix=".tmp", dir=settings_dir)
      with os.fdopen(fd, 'wb') as f:
        pickle.dump(config, f, protocol=pickle.HIGHEST_PROTOCOL)
      os.replace(tmp_path, config_path)
      logger.info("appendMode reset to True for stream=%s", stream)
    except Exception as e:
      logger.exception("Failed to update config appendMode for stream %s", stream)
      return jsonify({"error": f"Failed to update config: {e}"}), 500

  # --- (4) CSV에 데이터 추가 및 브로드캐스트 ---
  header = append_csv(stream, payload)
  broadcast(stream, payload)
  return jsonify(status="ok", stream=stream, columns=header)


@bp.route("/get_json_from_csv/<stream>", methods=['GET'])
def get_data(stream):
  try:
    limit = int(request.args.get("limit", "200"))
    fileName = request.args.get("fileName")
    if limit <= 0:
      limit = None
  except ValueError:
    limit = 200

  data = read_csv_as_json(stream, fileName=fileName,limit=limit)
  if not data:
    if os.path.exists(_csv_path(stream, fileName)):
      return jsonify([])  # 파일은 있지만 비어 있음
    abort(404, description="stream not found")
  return jsonify(data)


@bp.route("/sse_stream/<stream>", methods=['GET'])
def sse(stream):
  logger.info("SSE subscribe pid=%s stream=%s", os.getpid(), stream)

  def generate():
    channel = f"sse:{stream}"
    pubsub = redis.pubsub()
    pubsub.subscribe(channel)

    try:
      try:
        config = load_transmission_config(stream)
        send_interval_ms = config.get("sendInterval") or 10000
        send_interval_ms = int(send_interval_ms)
        keepalive_interval = (send_interval_ms / 1000.0) + 5
      except Exception as e:
        logger.warning("Failed to load config for %s, fallback keepalive", stream)
        keepalive_interval = 15

      init = {"event": "ready", "stream": stream, "ts": int(time.time())}
      yield f"event: ready\ndata: {json.dumps(init, ensure_ascii=False)}\n\n"

      last_keepalive = time.time()

      while True:
        message = pubsub.get_message(timeout=1.0)
        now = time.time()

        if message and message.get("type") == "message":
          yield f"data: {message['data']}\n\n"
          last_keepalive = now
        elif now - last_keepalive >= keepalive_interval:
          yield ": keepalive\n\n"
          last_keepalive = now
    finally:
      try:
        pubsub.close()
      except Exception:
        pass

  headers = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
  }
  return Response(generate(), mimetype="text/event-stream", headers=headers)


@bp.route("/get_iot_transmission_config/<macAddr>", methods=['GET'])
def get_iot_transmission_config(macAddr):
  try:
    payload = load_transmission_config(macAddr)
  except Exception as e:
    return jsonify({"error": str(e)}), 500
  return jsonify(payload), 200


@bp.route("/update_iot_transmission_config/", methods=['POST'])
@login_required
def update_iot_transmission_config():
  # 1) 입력 수집
  macAddr = request.args.get('macAddr')
  if not macAddr:
    return jsonify({"error": "쿼리스트링에 macAddr 값이 필요합니다."}), 400

  # JSON 본문 또는 폼 데이터 허용(기본은 JSON)
  if request.is_json:
    body = request.get_json(silent=True) or {}
  else:
    # 폼으로 들어온 경우에도 키를 동일하게 맞춰 사용
    body = request.form.to_dict()

  # 항상 필요한 필드만 itemgetter로 수집
  try:
    g = itemgetter('sendInterval', 'appendMode', 'sendLimitOn', 'useEndAt', 'chartWindow')
    sendInterval, appendMode, sendLimitOn, useEndAt, chartWindow = g(body)
  except KeyError as e:
    return jsonify({"error": f"누락된 필드: {e.args[0]}"}), 400

  # 선택 필드(활성화 시 필수)
  sendLimitCount = body.get('sendLimitCount', None)
  endAt_raw = body.get('endAt', None)

  # 2) 유효성 검증 및 형 변환
  def to_bool(v):
    if isinstance(v, bool):
      return v
    if isinstance(v, str):
      return v.strip().lower() in {'1', 'true', 't', 'yes', 'y', 'on'}
    if isinstance(v, (int, float)):
      return v != 0
    return False

  def to_int(v):
    try:
      return int(v)
    except Exception:
      return None

  errors = []

  # stream 정규화(파일명 안전)
  raw_macAddr = macAddr
  safe_macAddr = re.sub(r'[^A-Fa-f0-9]', '', raw_macAddr)  # 콜론 제거 포함
  if not safe_macAddr:
    errors.append("mac Address 형식이 올바르지 않습니다. (hex MAC 주소)")

  # 숫자 검증
  si = to_int(sendInterval)
  if si is None or si < 2000:
    errors.append("sendInterval은 2000 이상 정수여야 합니다.")

  cw = to_int(chartWindow)
  if cw is None or cw < 5 or cw > 200:
    errors.append("chartWindow는 5~200 사이의 정수여야 합니다.")

  am = to_bool(appendMode)
  slo = to_bool(sendLimitOn)
  uea = to_bool(useEndAt)

  slc = None
  if slo:
    slc = to_int(sendLimitCount)
    if slc is None or slc < 1:
      errors.append("sendLimitOn이 true인 경우 sendLimitCount는 1 이상의 정수여야 합니다.")

  # endAt 파싱 및 Asia/Seoul 변환
  endAt_dt_seoul = None
  if uea:
    if not endAt_raw:
      errors.append("useEndAt이 true인 경우 endAt이 필요합니다.")
    else:
      # ISO8601 문자열 가정. Z 지원을 위해 치환
      try:
        if isinstance(endAt_raw, (int, float)):
          # epoch milliseconds 또는 seconds로 들어올 수 있음
          # 10자리면 seconds, 13자리면 ms로 추정
          ts = float(endAt_raw)
          if ts > 1e12:  # ms
            dt_utc = datetime.fromtimestamp(ts / 1000.0, UTC)
          else:
            dt_utc = datetime.fromtimestamp(ts, UTC)
        elif isinstance(endAt_raw, str):
          s = endAt_raw.strip()
          # 'Z'를 표준 오프셋으로 변환
          if s.endswith('Z'):
            s = s[:-1] + '+00:00'
          # Python의 fromisoformat은 3.11 이전까지 일부 형식을 까다롭게 처리
          dt = None
          try:
            dt = datetime.fromisoformat(s)
          except ValueError:
            # 추가 포맷 허용(예: 'YYYY-MM-DD HH:MM:SS' 등)
            # 마지막 시도로 공백을 'T'로 교체
            s2 = s.replace(' ', 'T')
            dt = datetime.fromisoformat(s2)
          if dt.tzinfo is None:
            # 타임존 없는 경우 UTC로 가정
            dt_utc = dt.replace(tzinfo=UTC)
          else:
            dt_utc = dt.astimezone(UTC)
        else:
          raise ValueError("endAt 형식이 올바르지 않습니다.")
        
        seoul = tz('Asia/Seoul')
        endAt_dt_seoul = dt_utc.astimezone(seoul)
      except Exception:
        errors.append("endAt 파싱 실패: ISO8601 문자열(예: 2025-01-01T12:34:56Z)이어야 합니다.")

  if errors:
    return jsonify({"error": "유효성 검사 실패", "details": errors}), 400

  # 3) settings 디렉토리 보장
  settings_dir = os.path.join(root_dir, 'settings')
  try:
    os.makedirs(settings_dir, exist_ok=True)
  except Exception as e:
    return jsonify({"error": f"설정 디렉토리 생성 실패: {e}"}), 500

  # 4) 저장 객체(딕셔너리) 구성
  # endAt은 tz-aware datetime 그대로 저장(역직렬화 시 타입 유지). 필요하면 .isoformat()으로 문자열로 바꿔 저장 가능.
  config_obj = {
    "sendInterval": si,
    "appendMode": am,
    "sendLimitOn": slo,
    "sendLimitCount": slc if slo else None,
    "useEndAt": uea,
    "endAt": endAt_dt_seoul if uea else None,
    "chartWindow": cw,
  }

  # 5) pickle 파일 원자적 저장
  filename = f"{safe_macAddr}.p"
  final_path = os.path.join(settings_dir, filename)

  try:
    fd, tmp_path = tempfile.mkstemp(prefix=f"{safe_macAddr}.", suffix=".tmp", dir=settings_dir)
    try:
      with os.fdopen(fd, 'wb') as f:
        pickle.dump(config_obj, f, protocol=pickle.HIGHEST_PROTOCOL)
      os.replace(tmp_path, final_path)
    except Exception:
      # 파일 핸들 닫힌 후 예외 시 임시파일 제거 시도
      try:
        if os.path.exists(tmp_path):
          os.remove(tmp_path)
      finally:
        raise
  except Exception as e:
    return jsonify({"error": f"설정 저장 실패: {e}"}), 500

  # 6) 성공 응답
  return jsonify({"message": "IoT 전송 설정이 성공적으로 저장되었습니다.", "stream": safe_macAddr}), 200
