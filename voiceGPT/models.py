from voiceGPT import db
from datetime import datetime
from sqlalchemy import asc
from sqlalchemy.sql import func
import enum
from pytz import timezone as tz
from datetime import datetime, timezone

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

class RoleEnum(enum.Enum):
  user = "user"
  assistant = "assistant"

class Subject(db.Model):
  __tablename__ = 'subject'
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
  title = db.Column(db.String(150), nullable=False)
  system = db.Column(db.Text(), nullable=True)
  model = db.Column(db.String(100), nullable=False)
  range = db.Column(db.Integer, nullable=False)
  resolution = db.Column(db.Integer, nullable=False)
  dalle_model = db.Column(db.String(100), nullable=False)
  number_of_images = db.Column(db.Integer, nullable=False)
  quality_of_image = db.Column(db.String(100), nullable=False)
  size_of_image = db.Column(db.String(100), nullable=False)
  style_of_image = db.Column(db.String(100), nullable=False)
  create_date = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(tz('Asia/Seoul')))
  user = db.relationship('User', backref=db.backref('subjects', cascade='all, delete-orphan', lazy=True))
  messages = db.relationship('Message', backref='subject', cascade='all, delete-orphan', lazy=True)

class Message(db.Model):
  __tablename__ = 'message'
  id = db.Column(db.Integer, primary_key=True)
  subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False, index=True)
  create_date = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(tz('Asia/Seoul')))
  role = db.Column(db.Enum(RoleEnum), nullable=False)
  content = db.Column(db.Text(), nullable=False)
  msg_images = db.relationship('MsgImage', backref='message', cascade='all, delete-orphan', lazy=True)

class MsgImage(db.Model):
  __tablename__ = 'msg_image'
  id = db.Column(db.Integer, primary_key=True)
  message_id = db.Column(db.Integer, db.ForeignKey('message.id', ondelete='CASCADE'), nullable=True, index=True)
  imagePath = db.Column(db.String(300), nullable=False)
  thumbnailPath = db.Column(db.String(300), nullable=False)
  create_date = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(tz('Asia/Seoul')))

# 연도/학기 모델
class SchoolYearInfo(db.Model):
  __tablename__ = 'school_year_info'
  id = db.Column(db.Integer, primary_key=True)
  school_name = db.Column(db.String(100), nullable=False)
  year = db.Column(db.Integer, nullable=False)
  semester = db.Column(db.Integer, nullable=False)

  grades = db.relationship('SchoolGrade', back_populates='school_year_info', cascade="all, delete")


# 학년 모델
class SchoolGrade(db.Model):
  __tablename__ = 'school_grade'
  id = db.Column(db.Integer, primary_key=True)
  school_year_id = db.Column(db.Integer, db.ForeignKey('school_year_info.id'), nullable=False)
  grade = db.Column(db.String(10), nullable=False)

  school_year_info = db.relationship('SchoolYearInfo', back_populates='grades')
  classes = db.relationship('GradeClass', back_populates='school_grade', cascade="all, delete")
  subjects = db.relationship('GradeSubject', back_populates='school_grade', cascade="all, delete")

student_class_association = db.Table(
  'student_class_association',
  db.Column('student_id', db.Integer, db.ForeignKey('student.id'), primary_key=True),
  db.Column('grade_class_id', db.Integer, db.ForeignKey('grade_class.id'), primary_key=True)
)

# 학급 모델
class GradeClass(db.Model):
  __tablename__ = 'grade_class'
  id = db.Column(db.Integer, primary_key=True)
  school_grade_id = db.Column(db.Integer, db.ForeignKey('school_grade.id'), nullable=False)
  class_name = db.Column(db.String(10), nullable=False)
  created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(tz('Asia/Seoul')))
  updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')), onupdate=lambda: datetime.now(tz('Asia/Seoul')))

  school_grade = db.relationship('SchoolGrade', back_populates='classes')
  students = db.relationship(
      'Student',
      secondary=student_class_association,
      back_populates='classes'
  )


# 학생 모델
class Student(db.Model):
  __tablename__ = 'student'
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(50), nullable=False)
  sex = db.Column(db.String(10), nullable=False)
  date_of_birth = db.Column(db.Date, nullable=False)
  student_num = db.Column(db.String(20), unique=True, nullable=False)
  is_enrolled = db.Column(db.Boolean, default=True, nullable=False)
  untracked_date = db.Column(db.Date, nullable=True)

  evaluation_results = db.relationship('EvaluationResult', back_populates='student', cascade="all, delete-orphan")
  classes = db.relationship(
      'GradeClass',
      secondary=student_class_association,
      back_populates='students'
  )
  semester_summaries = db.relationship('SemesterSummary', back_populates='student', cascade="all, delete-orphan")
  subject_developments = db.relationship('SubjectDevelopment', back_populates='student', cascade="all, delete-orphan")
  observations = db.relationship('Observation', back_populates='student', cascade="all, delete-orphan")


# 교과 ENUM
class SubjectEnum(enum.Enum):
  KOREAN = "국어"
  MATH = "수학"
  ENGLISH = "영어"
  SCIENCE = "과학"
  SOCIAL = "사회"
  MUSIC = "음악"
  ART = "미술"
  PE = "체육"
  MORAL = "도덕"
  PRACTICAL = "실과"
  RIGHT_LIFE = "바른 생활"
  JOYFUL_LIFE = "즐거운 생활"
  WISE_LIFE = "슬기로운 생활"
  # 필요 시 추가


# 교과 모델
class GradeSubject(db.Model):
  __tablename__ = 'grade_subject'
  id = db.Column(db.Integer, primary_key=True)
  grade_id = db.Column(db.Integer, db.ForeignKey('school_grade.id'), nullable=False)
  subject = db.Column(db.Enum(SubjectEnum), nullable=False)
  is_active = db.Column(db.Boolean, default=False, nullable=False)

  school_grade = db.relationship('SchoolGrade', back_populates='subjects')
  assessment_areas = db.relationship('AssessmentArea', back_populates='grade_subject', cascade="all, delete")
  semester_summaries = db.relationship('SemesterSummary', back_populates='grade_subject', cascade="all, delete")
  subject_developments = db.relationship('SubjectDevelopment', back_populates='grade_subject', cascade="all, delete")
  observations = db.relationship('Observation', back_populates='grade_subject', cascade="all, delete")


# 평가영역 모델
class AssessmentArea(db.Model):
  __tablename__ = 'assessment_area'
  id = db.Column(db.Integer, primary_key=True)
  subject_id = db.Column(db.Integer, db.ForeignKey('grade_subject.id'), nullable=False)
  area = db.Column(db.String(50), nullable=False)

  grade_subject = db.relationship('GradeSubject', back_populates='assessment_areas')
  criteria = db.relationship('AchievementCriterion', back_populates='assessment_area', order_by=lambda: asc(AchievementCriterion.sort_order), cascade="all, delete")
  observations = db.relationship('Observation', back_populates='area', cascade="all, delete")


# 성취기준 모델
class AchievementCriterion(db.Model):
  __tablename__ = 'achievement_criterion'
  id = db.Column(db.Integer, primary_key=True)
  area_id = db.Column(db.Integer, db.ForeignKey('assessment_area.id'), nullable=False)
  criterion = db.Column(db.Text, nullable=False)
  evaluation_item = db.Column(db.Text, nullable=False)
  is_assessed = db.Column(db.Boolean, default=False, nullable=False)
  is_observed = db.Column(db.Boolean, default=False, nullable=False)
  sort_order = db.Column(db.Integer, default=100, nullable=False)

  assessment_area = db.relationship('AssessmentArea', back_populates='criteria')
  evaluation_criteria = db.relationship('EvaluationCriteria', back_populates='achievement_criterion', cascade="all, delete")
  evaluation_results = db.relationship('EvaluationResult', back_populates='achievement_criterion', cascade="all, delete-orphan")


# 평가기준 모델
class EvaluationCriteria(db.Model):
  __tablename__ = 'evaluation_criteria'
  id = db.Column(db.Integer, primary_key=True)
  criterion_id = db.Column(db.Integer, db.ForeignKey('achievement_criterion.id'), nullable=False)
  step = db.Column(db.Integer, nullable=False)
  level_name = db.Column(db.String(50), nullable=False)
  description = db.Column(db.Text)

  achievement_criterion = db.relationship('AchievementCriterion', back_populates='evaluation_criteria')


# 평가결과 모델
class EvaluationResult(db.Model):
  __tablename__ = 'evaluation_result'
  id = db.Column(db.Integer, primary_key=True)
  student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
  achievement_criterion_id = db.Column(db.Integer, db.ForeignKey('achievement_criterion.id'), nullable=False)
  level = db.Column(db.String(50), nullable=True)
  description = db.Column(db.Text)

  student = db.relationship('Student', back_populates='evaluation_results')
  achievement_criterion = db.relationship('AchievementCriterion', back_populates='evaluation_results')
  evidences = db.relationship('EvaluationEvidence', back_populates='evaluation_result', cascade="all, delete-orphan")


# 평가근거 모델
class EvaluationEvidence(db.Model):
  __tablename__ = 'evaluation_evidence'
  id = db.Column(db.Integer, primary_key=True)
  result_id = db.Column(db.Integer, db.ForeignKey('evaluation_result.id'), nullable=False)
  resource_path = db.Column(db.String(255), nullable=False)
  resource_type = db.Column(db.String(20))  # Optional: 'image', 'video', 'audio'
  created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')))
  updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')), onupdate=lambda: datetime.now(tz('Asia/Seoul')))

  evaluation_result = db.relationship('EvaluationResult', back_populates='evidences')


# 학기말종합의견 모델
class SemesterSummary(db.Model):
  __tablename__ = 'semester_summary'
  id = db.Column(db.Integer, primary_key=True)
  student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
  grade_subject_id = db.Column(db.Integer, db.ForeignKey('grade_subject.id'), nullable=False)
  description = db.Column(db.Text, nullable=False)
  created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')))
  updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')), onupdate=lambda: datetime.now(tz('Asia/Seoul')))

  student = db.relationship('Student', back_populates='semester_summaries')
  grade_subject = db.relationship('GradeSubject', back_populates='semester_summaries')

  __table_args__ = (
      db.UniqueConstraint('student_id', 'grade_subject_id', name='uq_student_subject_summary'),
  )


# 교과학습발달상황 모델
class SubjectDevelopment(db.Model):
  __tablename__ = 'subject_development'
  id = db.Column(db.Integer, primary_key=True)
  student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
  grade_subject_id = db.Column(db.Integer, db.ForeignKey('grade_subject.id'), nullable=False)
  description = db.Column(db.Text, nullable=False)
  created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')))
  updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')), onupdate=lambda: datetime.now(tz('Asia/Seoul')))

  student = db.relationship('Student', back_populates='subject_developments')
  grade_subject = db.relationship('GradeSubject', back_populates='subject_developments')


# 관찰 분류 ENUM
class ObservationClassification(enum.Enum):
  lesson = '수업내용'
  evaluation = '평가내용'
  uncategorized = '미선택'
  

# 관찰내용 모델
class Observation(db.Model):
  __tablename__ = 'observation'
  id = db.Column(db.Integer, primary_key=True)
  student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
  grade_subject_id = db.Column(db.Integer, db.ForeignKey('grade_subject.id'), nullable=False)
  description = db.Column(db.Text, nullable=False)
  classification = db.Column(db.Enum(ObservationClassification), nullable=False, default=ObservationClassification.uncategorized)
  area_id = db.Column(db.Integer, db.ForeignKey('assessment_area.id'), nullable=True)
  created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')))
  updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')), onupdate=lambda: datetime.now(tz('Asia/Seoul')))

  student = db.relationship('Student', back_populates='observations')
  grade_subject = db.relationship('GradeSubject', back_populates='observations')
  area = db.relationship('AssessmentArea', back_populates='observations')
  evidences = db.relationship('ObservationEvidence', back_populates='observation', cascade="all, delete")

# 관찰근거 모델
class ObservationEvidence(db.Model):
  __tablename__ = 'observation_evidence'
  id = db.Column(db.Integer, primary_key=True)
  observation_id = db.Column(db.Integer, db.ForeignKey('observation.id'), nullable=False)
  resource_path = db.Column(db.String(255), nullable=False)
  resource_type = db.Column(db.String(20))  # Optional: 'image', 'video', 'audio'
  created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')))
  updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(tz('Asia/Seoul')), onupdate=lambda: datetime.now(tz('Asia/Seoul')))

  observation = db.relationship('Observation', back_populates='evidences')