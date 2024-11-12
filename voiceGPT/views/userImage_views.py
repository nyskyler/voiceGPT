from flask import Blueprint, url_for, render_template, request, jsonify, g, flash, current_app, send_from_directory, abort
from datetime import datetime, timedelta
from sqlalchemy import func
from werkzeug.utils import redirect, secure_filename
from .. import db
from ..forms import ContentForm, UploadImageForm
from ..models import Content, Evaluation, User, content_voter, Option, Topic, UserImage
from .auth_views import login_required
from .chatgpt_views import get_setting_data
from pathlib import Path
from PIL import Image, ExifTags
import os

bp = Blueprint('userImage', __name__, url_prefix='/userImage')
timeLeft = 120

def deleteImagesOutdated():
  now = datetime.now()
  ago = now - timedelta(minutes=timeLeft)
  items_to_delete = UserImage.query.filter(UserImage.content_id == None, UserImage.create_date < ago).all() # 업로드만 하고 처리하지 않은 이미지가 많을 수 있으므로 first()가 아닌 all()
  if items_to_delete:
    for item in items_to_delete:
      item_path = Path(current_app.config["UPLOAD_FOLDER"], Path(item.imagePath).name)
      if item_path.exists():
        os.remove(item_path)  # 폴더에서 이미지 파일 삭제  
      db.session.delete(item)
    db.session.commit()

@bp.route("/get_image/<int:image_id>")
def get_imageFile(image_id):
	now = datetime.now()
	ago = now - timedelta(minutes=timeLeft)
	image_pending = UserImage.query.filter_by(id=image_id).first()
	if image_pending:
		image_path = Path(image_pending.imagePath).name
		try:
			return send_from_directory(current_app.config["UPLOAD_FOLDER"], image_path)
		except FileNotFoundError:
			abort(404)
	else:
		abort(404)

@bp.route("/get_imageId_by/<string:user_name>")
def get_imagePath(user_name):
	now = datetime.now()
	ago = now - timedelta(minutes=timeLeft)
	image_pending = UserImage.query.join(User).filter(
		User.username == user_name, 
		UserImage.content_id == None, 
		UserImage.create_date > ago).first()
	if image_pending:
		return jsonify({'imageId': image_pending.id})
	else:
		return jsonify({'message': 'No image found'}), 404
     
@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload_image():
	form = UploadImageForm()
	now = datetime.now()
	ago = now - timedelta(minutes=timeLeft)
	images_pending = UserImage.query.filter(UserImage.content_id == None, UserImage.create_date > ago).order_by(UserImage.create_date.desc())

	if request.method == "POST":
		deleteImagesOutdated()
		postFlag = UserImage.query.filter(UserImage.content_id == None, UserImage.user_id == g.user.id).first()
		if form.validate_on_submit() and not postFlag:
			file = form.image.data
			
			# 이미지 열기
			img = Image.open(file)

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
			
			# 이미지 크기 조정
			max_size = (512, 512)
			img.thumbnail(max_size, Image.LANCZOS)
			
			# 새로운 512x512 캔버스에 중앙 배치
			img_out = Image.new('RGB', (512, 512), (255, 255, 255))
			img_out.paste(img, ((512 - img.width) // 2, (512 - img.height) // 2))
			
			# 파일 확장자 처리
			ext = Path(file.filename).suffix.lower()
			if ext == '.heic':
				ext = '.png'
			formatted_now = now.strftime("%Y-%m-%d_%H-%M-%S")
			file_name = f'{g.user.username}_{form.subject.data}_{formatted_now}{ext}'
			image_path = Path(current_app.config["UPLOAD_FOLDER"], file_name)
			
			# 이미지 저장
			img_out.save(image_path)

			user_image = UserImage(
				user_id=g.user.id,
				subject=form.subject.data,
				imagePath='uploads/' + file_name,
				create_date=now,
			)
			
			db.session.add(user_image)
			db.session.commit()
			images_pending = UserImage.query.filter(UserImage.content_id == None, UserImage.create_date > ago).order_by(UserImage.create_date.desc())
		else:
			flash("이미 업로드하신 이미지가 있지 않나요? 또는 이미지 파일의 확장자가 (jpg, png, jpeg, heic)가 맞는지 확인하세요.")
				
	return render_template("userImage/userImage_list2.html", form=form, imagesPending=images_pending)


# @bp.route("/upload", methods=["GET", "POST"])
# @login_required
# def upload_image():
#   form = UploadImageForm()
#   now = datetime.now()
#   ago = now - timedelta(minutes=timeLeft)
#   images_pending = UserImage.query.filter(UserImage.content_id == None, UserImage.create_date > ago).order_by(UserImage.create_date.desc())
  
#   if request.method == "POST":
#     deleteImagesOutdated() 
#     postFlag = UserImage.query.filter(UserImage.content_id == None, UserImage.user_id == g.user.id).first()
#     if form.validate_on_submit() and not postFlag:
#       file = form.image.data
      
#       img = Image.open(file)

			#EXIF 데이터를 사용하여 이미지 회전
			# try:
			# 	for orientation in ExifTags.TAGS.keys():
			# 		if ExifTags.TAGS[orientation] == 'Orientation':
			# 				break
			# 	exif = img._getexif()
			# 	if exif is not None:
			# 		orientation = exif.get(orientation, None)
			# 		if orientation == 3:
			# 				img = img.rotate(180, expand=True)
			# 		elif orientation == 6:
			# 				img = img.rotate(270, expand=True)
			# 		elif orientation == 8:
			# 				img = img.rotate(90, expand=True)
			# except (AttributeError, KeyError, IndexError):
			# 	# EXIF 데이터가 없는 경우 처리
			# 	pass
#       horizontal_flag = True if img.width >= img.height else False
      
#       if horizontal_flag:
#         imgFrame = (512, int(img.height * (512/img.width)))
#       else:
#         imgFrame = (int(img.width * (512/img.height)), 512)
#       print(imgFrame)
        
#       img_resize = img.resize(imgFrame)
#       img_out = Image.new('RGB', (512,512), (255,255,255))
#       img_out.paste(img_resize, (int(256-(imgFrame[0]/2)), int(256-(imgFrame[1]/2))))
      
#       heicFlag = False
#       ext = Path(file.filename).suffix
#       formatted_now = now.strftime("%Y-%m-%d_%H-%M-%S")
#       if ext.lower() == '.heic':
#         ext = '.png'
#         heicFlag = True
#       file_name = f'{g.user.username}_{form.subject.data}_{formatted_now}' + ext
#       image_path = Path(current_app.config["UPLOAD_FOLDER"], file_name)
#       # if heicFlag:
#       #   img_out.convert('RGB').save(image_path)
#       # else:
#         # file.save(image_path)
#       img_out.save(image_path)
      
#       user_image = UserImage(
#         user_id = g.user.id,
#         subject = form.subject.data,
#         imagePath = 'uploads/' + file_name,
#         create_date = now,
# 			)
#       db.session.add(user_image)
#       db.session.commit()
#       images_pending = UserImage.query.filter(UserImage.content_id == None, UserImage.create_date > ago).order_by(UserImage.create_date.desc())
#     else:
#       flash("이미 업로드하신 이미지가 있지 않나요? 또는 이미지 파일의 확장자가 (jpg, png, jpeg, heic)가 맞는지 확인하세요.")
#     # GET 요청 처리
#   return render_template("userImage/userImage_list2.html", form=form, imagesPending=images_pending)

@bp.route("/deleteImage/<int:user_id>")
@login_required
def delete_image(user_id):
	item_to_delete = UserImage.query.filter(UserImage.content_id == None, UserImage.user_id == user_id).first()
	item_path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(item_to_delete.imagePath).name
	if item_path.exists():
		os.remove(item_path)  # 폴더에서 이미지 파일 삭제  
	db.session.delete(item_to_delete)
	db.session.commit()
	return redirect(url_for('userImage.upload_image'))
  