from flask import Flask
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData
from pillow_heif import register_heif_opener
import config

naming_convention = {
  "ix": "ix_%(column_0_label)s",
  "uq": "uq_%(table_name)s_%(column_0_name)s",
  "ck": "ck_%(table_name)s_%(column_0_name)s",
  "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s"
}

db = SQLAlchemy(metadata=MetaData(naming_convention=naming_convention))
migrate = Migrate()
csrf = CSRFProtect()

def create_app():
  app = Flask(__name__)
  app.config.from_object(config)
  app.config['WTF_CSRF_ENABLED'] = False
  csrf.init_app(app)

  #ORM
  db.init_app(app)
  if app.config['SQLALCHEMY_DATABASE_URI'].startswith("sqlite"):
    migrate.init_app(app, db, render_as_batch=True)
  else:
    migrate.init_app(app, db)
  from . import models

  # HEIF 파일 오프너 등록
  register_heif_opener()

  #블루프린트
  from .views import main_views, content_views, eval_views, auth_views, comment_views, vote_views, chatgpt_views, topic_views, userImage_views
  app.register_blueprint(main_views.bp)
  app.register_blueprint(content_views.bp)
  app.register_blueprint(eval_views.bp)
  app.register_blueprint(auth_views.bp)
  app.register_blueprint(comment_views.bp)
  app.register_blueprint(vote_views.bp)
  app.register_blueprint(chatgpt_views.bp)
  app.register_blueprint(topic_views.bp)
  app.register_blueprint(userImage_views.bp)

  #필터
  from .filter import format_datetime, markdown_to_html, shorten_text
  app.jinja_env.filters['datetime'] = format_datetime
  app.jinja_env.filters['mark'] = markdown_to_html
  app.jinja_env.filters['shorten'] = shorten_text

  return app
