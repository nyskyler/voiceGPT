from voiceGPT import db

content_voter = db.Table(
  'content_voter',
  db.Column('user_id', db.Integer, db.ForeignKey(
    'user.id', ondelete='CASCADE'), primary_key=True),
  db.Column('content_id', db.Integer, db.ForeignKey(
    'content.id', ondelete='CASCADE'), primary_key=True)
  )

evaluation_voter = db.Table(
  'evaluation_voter',
  db.Column('user_id', db.Integer, db.ForeignKey(
    'user.id', ondelete='CASCADE'), primary_key=True),
  db.Column('evaluation_id', db.Integer, db.ForeignKey(
    'evaluation.id', ondelete='CASCADE'), primary_key=True)
  )

class Content(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  question = db.Column(db.Text(), nullable=False)
  answer = db.Column(db.Text(), nullable=False)
  class_name = db.Column(db.String(50), nullable=True, server_default='운영자')
  create_date = db.Column(db.DateTime(), nullable=False)
  modify_date = db.Column(db.DateTime(), nullable=True)
  voter = db.relationship('User', secondary=content_voter, backref=db.backref('content_voter_set'))
  topic_id = db.Column(db.Integer, db.ForeignKey('topic.id', ondelete='CASCADE'), nullable=True)
  topic = db.relationship('Topic', backref=db.backref('content_set', cascade='all, delete-orphan'))

class Evaluation(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  content_id = db.Column(db.Integer, db.ForeignKey('content.id', ondelete='CASCADE'))
  content = db.relationship('Content', backref=db.backref('eval_set', cascade='all, delete-orphan'))
  comment = db.Column(db.Text(), nullable=False)
  create_date = db.Column(db.DateTime(), nullable=False)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
  user = db.relationship('User', backref=db.backref('eval_set', cascade='all, delete-orphan'))
  modify_date = db.Column(db.DateTime(), nullable=True)
  voter = db.relationship('User', secondary=evaluation_voter, backref=db.backref('evaluation_voter_set'))

class User(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  username = db.Column(db.String(150), unique=True, nullable=False)
  password = db.Column(db.String(200), nullable=False)

class Comment(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
  user = db.relationship('User', backref=db.backref('comment_set', cascade='all, delete-orphan'))
  content = db.Column(db.Text(), nullable=False)
  create_date = db.Column(db.DateTime(), nullable=False)
  modify_date = db.Column(db.DateTime())
  eval_id = db.Column(db.Integer, db.ForeignKey('evaluation.id', ondelete='CASCADE'), nullable=True)
  eval = db.relationship('Evaluation', backref=db.backref('comment_set'))

class Option(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  content_id = db.Column(db.Integer, db.ForeignKey('content.id', ondelete='CASCADE'))
  content = db.relationship('Content', backref=db.backref('option_set', cascade='all, delete-orphan'))
  max_tokens = db.Column(db.Integer, nullable=False)
  temperature = db.Column(db.Integer, nullable=False)
  top_p = db.Column(db.Integer, nullable=False)
  gpt_role = db.Column(db.Text(), nullable=True)

class Topic(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  deviceId = db.Column(db.Integer, nullable=False)
  title = db.Column(db.String(200), nullable=False)
  category = db.Column(db.String(100), nullable=False)
  create_date = db.Column(db.DateTime(), nullable=False)
  modify_date = db.Column(db.DateTime(), nullable=True)

class UserImage(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
  user = db.relationship('User', backref=db.backref('userImage_set', cascade='all, delete-orphan'))
  content_id = db.Column(db.Integer, db.ForeignKey('content.id', ondelete='CASCADE'), nullable=True)
  content = db.relationship('Content', backref=db.backref('userImage_set', cascade='all, delete-orphan'))
  subject = db.Column(db.String(200), nullable=False)
  imagePath = db.Column(db.String(300), nullable=False)
  create_date = db.Column(db.DateTime(), nullable=False)


