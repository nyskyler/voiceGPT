from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import TextAreaField, StringField, PasswordField, BooleanField
from wtforms.fields import EmailField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo, Email, Optional

class ContentForm(FlaskForm):
  question = TextAreaField('질문', validators=[DataRequired('질문은 필수입력 항목입니다.')])
  answer = TextAreaField('답변', validators=[DataRequired('답변은 필수입력 항목입니다.')])
  class_name = StringField('학급', validators=[DataRequired('학급은 필수입력 항목입니다.')])

class EvaluationForm(FlaskForm):
  comment = TextAreaField('내용', validators=[DataRequired('내용은 필수입력 항목입니다.')])

class UserCreateForm(FlaskForm):
  username = StringField('사용자이름', validators=[DataRequired(), Length(min=3, max=25)])
  password1 = PasswordField('비밀번호', validators=[
    DataRequired(), EqualTo('password2', '비밀번호가 일치하지 않습니다.')])
  password2 = PasswordField('비밀번호확인', validators=[DataRequired()])

class UserLoginForm(FlaskForm):
  username = StringField('사용자이름', validators=[DataRequired(), Length(min=3, max=25)])
  password = PasswordField('비밀번호', validators=[DataRequired()])

class CommentForm(FlaskForm):
  content = TextAreaField('내용', validators=[DataRequired()])

class SettingForm(FlaskForm):
  ssid = StringField('네트워크 이름', validators=[DataRequired()])
  psk = StringField('네트워크 암호', validators=[DataRequired()])
  max_tokens = StringField('최대 답변 글자 수', validators=[DataRequired()])
  gpt_role = TextAreaField('GPT 역할', validators=[DataRequired()])
  temperature = StringField('텍스트 다양성 조절 지수', validators=[DataRequired()])
  top_p = StringField('확률 상위 퍼센트 컷오프', validators=[DataRequired()])
  not_upload = BooleanField('서버에 데이터 미반영')
  verifying_user_input = BooleanField('음성 입력 검증')
  conversation_continuity = BooleanField('대화 연속성 유지')
  input_lang = StringField('입력 언어', validators=[DataRequired(), Length(max=10)])
  output_lang = StringField('출력 언어', validators=[DataRequired(), Length(max=10)])
  topic = StringField('대화 주제')
  topic_category = StringField('주제 분류')

  def validate(self, extra_validators=None):
    if not super(SettingForm, self).validate(extra_validators):
      return False
    
    if self.conversation_continuity.data:
      if not self.topic.data or not self.topic_category.data:
        self.topic.errors.append('대화 주제와 분류는 필수 항목입니다.')
        return False
      
    return True

class UploadImageForm(FlaskForm):
  # 파일 업로드에 필요한 유효성 검증을 설정한다.
  subject = StringField('주재', validators=[DataRequired('주제는 필수입력 항목입니다.'), Length(max=20)])
  image = FileField(
    validators=[
      FileRequired("이미지 파일을 지정해 주세요."),
      FileAllowed(["png", "jpg", "jpeg", "heic", "webp"], "지원되지 않는 이미지 형식입니다."),
    ]
  )
