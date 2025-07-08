from flask import Blueprint, jsonify, url_for, render_template, flash, request, g, current_app, send_from_directory, abort, send_file, session
from werkzeug.utils import redirect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from operator import itemgetter 
from collections import defaultdict
import os
import json
import configparser
import unicodedata
import glob
import csv
import pprint
import io
import base64
import traceback
from .. import db
from dotenv import load_dotenv
from .auth_views import login_required
from ..models import SchoolYearInfo, SchoolGrade, SubjectEnum, GradeSubject, GradeClass, Student, student_class_association, AssessmentArea, AchievementCriterion, EvaluationCriteria, EvaluationResult, EvaluationEvidence, Observation, ObservationEvidence, ObservationClassification
from pathlib import Path
from PIL import Image, ExifTags
from datetime import datetime, timedelta, timezone
from pytz import timezone as tz


config = configparser.ConfigParser()
config.read('textgpt.ini')
authorized_users = config['USER']['MEMBER']
user_list = [user.strip() for user in authorized_users.split(',') if user.strip()]

bp = Blueprint('neis', __name__, url_prefix='/neis')
root_dir = Path('/Volumes/X31')

grade_subject_map = {
  ('1학년', '2학년'): ['국어', '수학', '바른 생활', '즐거운 생활', '슬기로운 생활'],
  ('3학년', '4학년'): ['국어', '도덕', '사회', '수학', '과학', '체육', '음악', '미술', '영어'],
  ('5학년', '6학년'): ['국어', '도덕', '사회', '수학', '과학', '실과', '체육', '음악', '미술', '영어'],
}

def normalize_path(path, form="NFC"):
  """MacOS에서 한글 파일명을 정상 처리하도록 정규화"""
  return unicodedata.normalize(form, path)

@bp.route("/main/")
@login_required
def main():
  if g.user.username not in user_list:
    flash('NEIS는 인가받은 사용자만 이용가능합니다. 관리자에게 문의하세요.')
    return redirect(url_for('main.index'))
  
  gs = GradeSubject.query.filter_by(is_active=True).first()
  sg = SchoolGrade.query.filter_by(id=gs.grade_id).first()
  syi = SchoolYearInfo.query.filter_by(id=sg.school_year_id).first()

  session['active_school_info'] = {
    'school': syi.school_name,
    'year': syi.year,
    'semester': syi.semester,
  }
  
  return render_template('neis/neis.html')


@bp.route("/getRegisteredSubjects/", methods=['GET'])
@login_required
def getRegisteredSubjects():
  activeSubjects = []
  inactiveSubjects = []
  try:
    subjects = GradeSubject.query.options(
      joinedload(GradeSubject.school_grade)
      .joinedload(SchoolGrade.school_year_info)
    ).all()

    for subject in subjects:
      # 연쇄 참조로 정보 접근 (N+1 쿼리 걱정 없음: joinedload)
      base_data = {
        'school': subject.school_grade.school_year_info.school_name,
        'year': subject.school_grade.school_year_info.year,
        'semester': subject.school_grade.school_year_info.semester,
        'grade': subject.school_grade.grade,
        'subject': subject.subject.value,
      }

      if subject.is_active:
        activeSubjects.append({**base_data})
      else:
        inactiveSubjects.append({**base_data, 'isRecordSavedToDb': True})

    return jsonify({
      "message": "등록된 교과 조회 완료",
      "activeSubjects": activeSubjects,
      "inactiveSubjects": inactiveSubjects,
    }), 200

  except SQLAlchemyError as e:
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  
  except Exception as e:
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500
    

@bp.route("/createNewAcademicRecords/", methods=['POST'])
@login_required
def createNewAcademicRecords():
  data = request.get_json()
  if not data:
    return jsonify({"error": "Invalid request."}), 400

  try:
    successful_indices = []
    failed_indices = []
    invalid_subjects = []

    for idx, row in enumerate(data):
      _school, _year, _semester, _grade, _subject = itemgetter('school', 'year', 'semester', 'grade', 'subject')(row)
      
      # 1. 연도/학기 형변환 및 유효성 확인
      try:
        _year = int(_year)
      except ValueError:
        failed_indices.append(idx)
        continue

      _semester = int(_semester)
      if _semester is None:
        failed_indices.append(idx)
        continue  # skip invalid semester

      # 2. '담임' 표기 처리 : 여러 과목 등록
      subject_list = []
      if _subject == '담임':
        for grades, subjects in grade_subject_map.items():
          if _grade in grades:
            subject_list = subjects
            break
      else:
        subject_list = [_subject]

      # 3. 연도/학기 정보 조회/생성
      syi = SchoolYearInfo.query.filter_by(school_name=_school, year=_year, semester=_semester).first()
      if not syi:
        syi = SchoolYearInfo(school_name=_school, year=_year, semester=_semester)
        db.session.add(syi)
        db.session.flush()  # syi.id 할당

      # 4. 학년 정보 조회/생성
      grade = SchoolGrade.query.filter_by(grade=_grade, school_year_id=syi.id).first()
      if not grade:
        grade = SchoolGrade(grade=_grade, school_year_id=syi.id)
        db.session.add(grade)
        db.session.flush()  # grade.id 할당

      # 5. 과목 항목 생성
      has_valid_subject = False
      for sub in subject_list:
        try:
          enum_val = SubjectEnum(sub)
        except ValueError:
          invalid_subjects.append({'index': idx, 'subject': sub})
          continue  # 무효 과목은 건너뜀

        # 이미 존재하는지 검사
        exist_subject = GradeSubject.query.filter_by(grade_id=grade.id, subject=enum_val).first()
        if not exist_subject:
          gs = GradeSubject(grade_id=grade.id, subject=enum_val)
          db.session.add(gs)
        has_valid_subject = True

      # 최종 성공 여부 기록
      if has_valid_subject:
        successful_indices.append(idx)
      else:
        failed_indices.append(idx)

    db.session.commit()
    return jsonify({
      "message": "처리 완료",
      "successful_indices": successful_indices,
      "failed_indices": failed_indices,
      "invalid_subjects": invalid_subjects,
    }), 200

  except SQLAlchemyError as e:
    db.session.rollback()
    return jsonify({'error': 'Database error', 'message': str(e)}), 500
  
  except Exception as e:
    db.session.rollback()
    print(f"An unexpected error occurred: {e}")
    return jsonify({"message": "An unexpected error occurred"}), 500


@bp.route("/updateSubjectActivation/", methods=['PATCH'])
@login_required
def update_subject_activation():
  data = request.get_json()
  if not isinstance(data, list):
    return jsonify({"error": "Invalid request format. Expected a list."}), 400

  try:
    updated = []

    for item in data:
      school = item.get('school')
      year = int(item.get('year'))
      semester = item.get('semester')
      grade = item.get('grade')
      subject_str = item.get('subject')
      is_active = item.get('is_active')

      # 예외 처리: '1학기' → 1
      if isinstance(semester, str) and '학기' in semester:
        semester = int(semester.replace('학기', '').strip())
      else:
        semester = int(semester)

      if not isinstance(is_active, bool):
        continue  # is_active가 True/False가 아닌 경우는 무시

      # Step 1: SchoolYearInfo 찾기
      school_year = SchoolYearInfo.query.filter_by(
        school_name=school, year=year, semester=semester
      ).first()
      if not school_year:
        continue

      # Step 2: SchoolGrade 찾기
      school_grade = SchoolGrade.query.filter_by(
        grade=grade, school_year_id=school_year.id
      ).first()
      if not school_grade:
        continue

      # Step 3: GradeSubject 찾기
      try:
        subject_enum = SubjectEnum(subject_str)
      except ValueError:
        continue  # 잘못된 과목 문자열

      grade_subject = GradeSubject.query.filter_by(
        grade_id=school_grade.id, subject=subject_enum
      ).first()
      if not grade_subject:
        continue

      # Step 4: 값 변경
      if grade_subject.is_active != is_active:
        grade_subject.is_active = is_active
        updated.append({
          "grade_subject_id": grade_subject.id,
          "new_status": is_active
        })

    db.session.commit()

    if updated:
      session['active_school_info'] = {
        'school': school,
        'year': year,
        'semester': semester,
      }

    return jsonify({
      "message": f"{len(updated)}개의 교과가 업데이트되었습니다.",
      "updated": updated
    }), 200

  except Exception as e:
    db.session.rollback()
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  

@bp.route("/analyzeStudentListByClassInfo/", methods=['GET'])
@login_required
def analyzeStudentListByClassInfo():
  # 1. 입력값 얻기 및 검증
  school = request.args.get('school')
  year = request.args.get('year')
  semester = request.args.get('semester')
  grades = request.args.getlist('grades')

  if not (school and year and semester and grades):
    return jsonify({
      "error": "Missing required parameters.",
      "detail": {
        "school": school,
        "year": year,
        "semester": semester,
        "grades": grades
      }
    }), 400

  # 2. 파일 경로 체크
  target_path = os.path.join(
    str(root_dir), "NEIS", school, year, f"{semester}학기", "학생명렬표"
  )

  if not os.path.exists(target_path):
    return jsonify({
      "error": f"Path not found: {target_path}",
      "your_input": {
          "school": school, "year": year, "semester": semester
      }
    }), 404

  class_list = []

  # 3. 학년별 파일 처리(반 이름과 학생 수)
  for grade in grades:
    normalized_grade = normalize_path(grade, "NFD")
    # 학년 포함된 모든 csv 파일 검색
    pattern = os.path.join(target_path, f"*{normalized_grade}*.csv")
    for filename in glob.glob(pattern):
      try:
        print(filename)
        with open(filename, encoding='cp949') as f:
          reader = csv.reader(f)
          header = next(reader, None)  # 헤더 row (없으면 None)
          rows = list(reader)
          if rows:
            class_name = rows[0][1] if len(rows[0]) > 1 else ""
            num_of_students = len(rows)
          else:
            class_name = ""
            num_of_students = 0
          class_list.append({
            'year': year,
            'grade': grade,
            'className': class_name,
            'numOfStudents': num_of_students,
            'created_at': '',
            'updated_at': '',
          })
      except Exception as e:
        # 파일 읽기/파싱 실패 등 예외 처리(무시하거나 로거로 기록)
        continue

  return jsonify({
    "message": f"Path({target_path}) exists.",
    "classList": class_list,
    "grades": grades
  }), 200


@bp.route("/getGradeClassesAndStudentCounts/", methods=['GET'])
@login_required
def getGradeClassesAndStudentCounts():
  school = request.args.get('school')
  year = request.args.get('year')
  semester = request.args.get('semester')
  
  if not (school and year and semester):
    return jsonify({
      "error": "Missing required parameters.",
      "detail": {
        "school": school,
        "year": year,
        "semester": semester,
      }
    }), 400
  
  # 예외 처리: '1학기' → 1
  if isinstance(semester, str) and '학기' in semester:
    semester = int(semester.replace('학기', '').strip())
  else:
    semester = int(semester)

  try: 
    school_year = SchoolYearInfo.query.filter_by(school_name=school, year=year, semester=semester).first()

    if not school_year:
      return jsonify({"error": "SchoolYearInfo not found."}), 400

    school_grades = SchoolGrade.query.filter_by(school_year_id=school_year.id).all()
    grade_class_info_list = []

    for school_grade in school_grades:
      for cls in school_grade.classes:
        student_count = (
          db.session.query(func.count(student_class_association.c.student_id))
          .filter(student_class_association.c.grade_class_id == cls.id)
          .scalar()
        )

        grade_class_info_list.append({
          'year': year,
          'grade': school_grade.grade,
          'className': cls.class_name,
          'numOfStudents': student_count,
          'created_at': cls.created_at.strftime("%Y.%m.%d.") if cls.created_at else None,
          'updated_at': cls.updated_at.strftime("%Y.%m.%d.") if cls.updated_at else None,
        }) 

    return jsonify({
      "message": f"{school} {year}년 {semester}학기 학급 정보 조회 완료",
      "classes": grade_class_info_list,
      "total": len(grade_class_info_list)
    }), 200
    
  except Exception as e:
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500


@bp.route("/processGradeClassAndStudentRecords/", methods=['POST'])
@login_required
def processGradeClassAndStudentRecords():
  data = request.get_json()
  if not isinstance(data, list):
    return jsonify({"error": "Invalid request format. Expected a list."}), 400
  
  missing_files = []
  created_classes = 0
  created_students = 0
  linked_relations = 0
  
  try:
    for item in data:
      school = item.get('school')
      year = int(item.get('year'))
      semester = item.get('semester')
      grade = item.get('grade')
      class_name = item.get('className')

      should_update_grade_class = False
      is_new_grade_class = False

      # 예외 처리: '1학기' → 1
      if isinstance(semester, str) and '학기' in semester:
        semester = int(semester.replace('학기', '').strip())
      else:
        semester = int(semester)

      # Step 1: SchoolYearInfo 찾기
      school_year = SchoolYearInfo.query.filter_by(
        school_name=school, year=year, semester=semester
      ).first()
      if not school_year:
        continue

      # Step 2: SchoolGrade 찾기
      school_grade = SchoolGrade.query.filter_by(
        grade=grade, school_year_id=school_year.id
      ).first()
      if not school_grade:
        continue

      # Step 3: 학급명 csv 파일 찾기  
      target_path = os.path.join(
        str(root_dir), "NEIS", school, str(year), f"{semester}학기", "학생명렬표"
      )

      normalized_grade = normalize_path(grade, "NFD")
      normalized_class = normalize_path(class_name, "NFD")
      file_path = os.path.join(target_path, f"{normalized_grade}_{normalized_class}.csv")

      if not os.path.exists(file_path):
        missing_files.append(file_path)
        continue

      # Step 4: GradeClass 찾기 
      gc = GradeClass.query.filter_by(school_grade_id=school_grade.id, class_name=class_name).first()
      if not gc:
        gc = GradeClass(school_grade_id=school_grade.id, class_name=class_name)
        db.session.add(gc)
        db.session.flush()  # gc.id 할당
        created_classes += 1
        is_new_grade_class = True
      
      # Step 5: csv 파일을 순회하며 행마다 필요한 학생 정보(이름, 학생개인번호, 성별, 생년월일)를 수집하여 딕셔너리를 요소로 하는 리스트 생성하기
      student_info_list = []

      try:
        with open(file_path, encoding='cp949') as f:
          reader = csv.reader(f)
          header = next(reader, None)  # 헤더 row (없으면 None)
          for row in reader:
            student_info_list.append({
              'name': row[3],
              'student_num': row[4],
              'sex': row[5],
              'date_of_birth': row[6],
            })
        # pprint.pprint(student_info_list)
      except Exception as e:
        missing_files.append(file_path + f" (읽기 실패: {e})")
        continue

      # Step 6: 학생 정보 리스트를 순회하며 Student 레코드 생성 및 관계 짓기
      for si in student_info_list:
        _name, _sex, _date_of_birth, _student_num = itemgetter('name', 'sex', 'date_of_birth', 'student_num')(si)
        _date_of_birth = datetime.strptime(_date_of_birth, "%Y.%m.%d.").date()
        
        student = Student.query.filter_by(name=_name, sex=_sex, date_of_birth=_date_of_birth, student_num=_student_num).first()
        
        if not student:
          student = Student(name=_name, sex=_sex, date_of_birth=_date_of_birth, student_num=_student_num)
          db.session.add(student)
          db.session.flush()  # student.id 할당
          created_students += 1
        
        if gc not in student.classes:
          student.classes.append(gc)  # 관계(student.classes.append(...))를 맺을 때 내부적으로 student.id 값이 필요함
          linked_relations += 1
          should_update_grade_class = True

      if should_update_grade_class and not is_new_grade_class:
        gc.updated_at = datetime.now(tz('Asia/Seoul'))
        
    db.session.commit()
    return jsonify({
      "message": "처리 완료",
      "created_classes": created_classes,
      "created_students": created_students,
      "linked_relations": linked_relations,
      "missing_files": missing_files,
    }), 200

  except Exception as e:
    db.session.rollback()
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  
  
@bp.route("/deleteGradeClassBySchoolYearSemesterAndName/", methods=['DELETE'])
@login_required
def deleteGradeClassBySchoolYearSemesterAndName():
  data = request.get_json()
  if not isinstance(data, list):
    return jsonify({"error": "Invalid request format. Expected a list."}), 400

  deleted = []
  not_found = []

  try:
    for item in data:
      school = item.get('school')
      year = int(item.get('year'))
      semester = item.get('semester')
      grade = item.get('grade')
      class_name = item.get('className')

      # 예외 처리: '1학기' → 1
      if isinstance(semester, str) and '학기' in semester:
        semester = int(semester.replace('학기', '').strip())
      else:
        semester = int(semester)

      # Step 1: SchoolYearInfo 찾기
      school_year = SchoolYearInfo.query.filter_by(
        school_name=school, year=year, semester=semester
      ).first()
      if not school_year:
        not_found.append(item)
        continue

      # Step 2: SchoolGrade 찾기
      school_grade = SchoolGrade.query.filter_by(
        grade=grade, school_year_id=school_year.id
      ).first()
      if not school_grade:
        not_found.append(item)
        continue

      # Step 3: GradeClass 찾기 
      gc = GradeClass.query.filter_by(
        school_grade_id=school_grade.id, class_name=class_name
      ).first()
      if not gc:
        not_found.append(item)
        continue

      db.session.delete(gc)
      deleted.append(item)

    db.session.commit()
    return jsonify({
      "message": "삭제 완료",
      "deleted": deleted,
      "not_found": not_found
    }), 200

  except Exception as e:
    db.session.rollback()
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500


def find_students_orm_way(school_year_info_id, partial_name, date_of_birth=None, is_enrolled=True):
  Grade = aliased(SchoolGrade)
  Class = aliased(GradeClass)
  StudentAlias = aliased(Student)

  base_query = (
    db.session.query(
      StudentAlias.name,
      StudentAlias.is_enrolled,
      StudentAlias.date_of_birth,
      StudentAlias.student_num,
      StudentAlias.untracked_date,
      Class.class_name,
      Grade.grade
    )
    .join(student_class_association, StudentAlias.id == student_class_association.c.student_id)
    .join(Class, student_class_association.c.grade_class_id == Class.id)
    .join(Grade, Class.school_grade_id == Grade.id)
    .join(SchoolYearInfo, Grade.school_year_id == SchoolYearInfo.id)
    .filter(
      SchoolYearInfo.id == school_year_info_id,
      StudentAlias.name.contains(partial_name),
      StudentAlias.is_enrolled == is_enrolled
    )
  )

  if date_of_birth:
    base_query = base_query.filter(StudentAlias.date_of_birth == date_of_birth)

  results = base_query.all()

  return [
    {
      "name": r.name,
      "is_enrolled": r.is_enrolled,
      "date_of_birth": r.date_of_birth.strftime("%Y.%m.%d."),
      "student_num": r.student_num,
      "untracked_date": r.untracked_date.strftime("%Y.%m.%d.") if r.untracked_date else '', 
      "class_name": r.class_name,
      "grade": r.grade
    }
    for r in results
  ]




@bp.route("/search_students_by_partial_name_and_birthdate/", methods=['GET'])
@login_required
def search_students_by_partial_name_and_birthdate():
  school_info = session.get('active_school_info')
  if not school_info:
    return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

  school = school_info['school']
  year = school_info['year']
  semester = school_info['semester']

  name = request.args.get('name', '').strip()
  dob_str = request.args.get('dob', '').strip()

  dob = None
  if dob_str:
    try:
      dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except ValueError:
      return jsonify({"error": "날짜 형식이 올바르지 않습니다. (예: YYYY-MM-DD)"}), 400

  try:
    syi = SchoolYearInfo.query.filter_by(school_name=school, year=year, semester=semester).first()
    if not syi:
      return jsonify({"error": "해당 학기 정보를 찾을 수 없습니다."}), 404

    is_enrolled = False if not name and not dob else True

    student_list = find_students_orm_way(syi.id, name, dob, is_enrolled)

    return jsonify({
      "message": f"{school} {year}년 {semester}학기 학생 정보 조회 완료",
      "student_list": student_list
    }), 200

  except Exception as e:
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  


@bp.route("/toggle_student_enrollment_status_bulk/", methods=['PATCH'])
@login_required
def toggle_student_enrollment_status_bulk():
  data = request.get_json()
  if not isinstance(data, list):
    return jsonify({"error": "Invalid request format. Expected a list."}), 400

  updated = []
  skipped = []

  try:
    for item in data:
      try:
        _name = item.get('name')
        _student_num = item.get('student_num')
        dob_str = item.get('date_of_birth')
        _is_enrolled = item.get('is_enrolled')

        # 필수값 검증
        if not all([_name, _student_num, dob_str]) or not isinstance(_is_enrolled, bool):
          skipped.append({"item": item, "reason": "Invalid fields"})
          continue

        # 생년월일 파싱
        _date_of_birth = datetime.strptime(dob_str, "%Y.%m.%d.").date()
        new_status = not _is_enrolled

        student = Student.query.filter_by(
          name=_name,
          student_num=_student_num,
          date_of_birth=_date_of_birth
        ).first()

        if not student:
          skipped.append({"item": item, "reason": "Student not found"})
          continue

        student.is_enrolled = new_status
        student.untracked_date = None if new_status else datetime.now(tz('Asia/Seoul'))

        updated.append({
          "name": _name,
          "student_num": _student_num,
          "date_of_birth": dob_str,
          "is_enrolled": new_status,
          "untracked_date": student.untracked_date.strftime("%Y.%m.%d.") if student.untracked_date else '',
        })
      except Exception as inner_e:
        skipped.append({"item": item, "reason": f"Error: {str(inner_e)}"})

    db.session.commit()

    return jsonify({
      "message": f"{len(updated)}명 학생의 재학여부가 업데이트되었습니다.",
      "updated": updated,
      "skipped": skipped
    }), 200

  except Exception as e:
    db.session.rollback()
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  

@bp.route("/get_active_school_info/", methods=['GET'])
@login_required
def get_active_school_info():
  school_info = session.get('active_school_info')
  if not school_info:
    return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

  school = school_info['school']
  year = school_info['year']
  semester = school_info['semester']

  return jsonify({
    "school": school,
    "year": year,
    "semester": semester,
  }), 200
  

@bp.route("/get_active_grades/", methods=['GET'])
@login_required
def get_active_grades():
  school_info = session.get('active_school_info')
  if not school_info:
    return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

  school = school_info['school']
  year = school_info['year']
  semester = school_info['semester']

  try: 
    school_year = SchoolYearInfo.query.filter_by(
      school_name=school, year=year, semester=semester
    ).first()

    if not school_year:
      return jsonify({"error": "SchoolYearInfo not found."}), 400
    
    school_grades = SchoolGrade.query.filter_by(
      school_year_id=school_year.id
    ).order_by("grade").all()

    if not school_grades:
      return jsonify({"error": "SchoolGrade not found."}), 400
    
    active_grades = [sg.grade for sg in school_grades]
    
    return jsonify({
      "year": year,
      "semester": semester,
      "active_grades": active_grades
    }), 200

  except Exception as e:
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  


@bp.route("/get_subjects_by_grade/<string:_grade>", methods=['GET'])
@login_required
def get_subjects_by_grade(_grade):
  school_info = session.get('active_school_info')
  if not school_info:
    return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

  school, year, semester = itemgetter('school', 'year', 'semester')(school_info)

  try: 
    school_year = SchoolYearInfo.query.filter_by(
      school_name=school, year=year, semester=semester
    ).first()

    if not school_year:
      return jsonify({"error": "SchoolYearInfo not found."}), 400
    
    school_grade = SchoolGrade.query.filter_by(
      school_year_id=school_year.id, grade=_grade
    ).first()

    if not school_grade:
      return jsonify({"error": "SchoolGrade not found."}), 400
    
    subjects = [gs.subject.value for gs in school_grade.subjects]
    classes = [[cs.class_name, cs.id] for cs in school_grade.classes]
    
    return jsonify({
      "grade": _grade,
      "subjects": subjects,
      "classes": sorted(classes, reverse = True)
    }), 200

  except Exception as e:
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  


@bp.route("/get_fields_by_subject/", methods=['GET'])
@login_required
def get_fields_by_subject():
  _grade = request.args.get('grade')
  _subject = request.args.get('subject')

  if not (_grade and _subject):
    return jsonify({
      "error": "Missing required parameters.",
      "detail": {
        "grade": _grade,
        "subject": _subject,
      }
    }), 400

  school_info = session.get('active_school_info')
  if not school_info:
    return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

  try:
    school, year, semester = itemgetter('school', 'year', 'semester')(school_info)
    
    school_year = SchoolYearInfo.query.filter_by(
      school_name=school, year=year, semester=semester
    ).first()
    if not school_year:
      return jsonify({"error": "SchoolYearInfo not found."}), 400

    school_grade = SchoolGrade.query.filter_by(
      school_year_id=school_year.id, grade=_grade
    ).first()
    if not school_grade:
      return jsonify({"error": "SchoolGrade not found."}), 400

    try:
      enum_subject = SubjectEnum(_subject)  # 문자열 → Enum 변환
    except ValueError:
      return jsonify({"error": f"Invalid subject value: {_subject}"}), 400

    grade_subject = GradeSubject.query.filter_by(
      grade_id=school_grade.id, subject=enum_subject
    ).first()
    if not grade_subject:
      return jsonify({"error": "GradeSubject not found."}), 400

    fields = [(field.area, len(field.criteria), field.id) for field in grade_subject.assessment_areas]
    if not fields:
      fields = [('-없음-', 0)]
    else:
      fields.insert(0, ('-전체-', 0))

    return jsonify({
      "subject": _subject,
      "fields": fields,
    }), 200

  except Exception as e:
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  

@bp.route("/manage_assessment_areas_by_grade_subject/", methods=['POST'])
@login_required
def manage_assessment_areas_by_grade_subject():
  data = request.get_json()
  if not isinstance(data, dict):
    return jsonify({"error": "Invalid request format. Expected a dict."}), 400

  school_info = session.get('active_school_info')
  if not school_info:
    return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

  try:
    school, year, semester = itemgetter('school', 'year', 'semester')(school_info)
    grade, subject = itemgetter('grade', 'subject')(data['extraInfo'])
    del data['extraInfo']

    school_year = SchoolYearInfo.query.filter_by(
      school_name=school, year=year, semester=semester
    ).first()
    if not school_year:
      return jsonify({"error": "SchoolYearInfo not found."}), 400

    school_grade = SchoolGrade.query.filter_by(
      school_year_id=school_year.id, grade=grade
    ).first()
    if not school_grade:
      return jsonify({"error": "SchoolGrade not found."}), 400

    try:
      enum_subject = SubjectEnum(subject)  # 문자열 → Enum 변환
    except ValueError:
      return jsonify({"error": f"Invalid subject value: {subject}"}), 400

    grade_subject = GradeSubject.query.filter_by(
      grade_id=school_grade.id, subject=enum_subject
    ).first()

    if not grade_subject:
      return jsonify({"error": "GradeSubject not found."}), 400  
    
    fieldsList = AssessmentArea.query.filter_by(subject_id=grade_subject.id)
    keys_to_delete = []
    for idx, field in enumerate(fieldsList, start=1):
      idx = str(idx)
      if idx in data:
        if data[idx] != field.area:
          field.area = data[idx]
        keys_to_delete.append(idx)
      else:
        db.session.delete(field)

    for k in keys_to_delete:
      del data[k]

    for val in data.values():
      field = AssessmentArea(
        subject_id=grade_subject.id,
        area=val
      )
      db.session.add(field)

    db.session.commit()
    return jsonify({
      "message": "영역명 처리 완료",
    }), 200
  except Exception as e:
    db.session.rollback()
    print(f"오류 발생: {e}")
    return jsonify({"error": "Server error", "details": str(e)}), 500
  

# 💡 공통 에러 응답 유틸
def error_response(message, code=400, detail=None):
  response = {"error": message}
  if detail:
    response["detail"] = detail
  return jsonify(response), code

# 💡 중복된 학사정보 조회 로직 정리
class SchoolContext:
  def __init__(self, session_data, grade_str, subject_str):
    self.school_info = session_data
    self.grade_str = grade_str
    self.subject_str = subject_str

    self.school_year = None
    self.school_grade = None
    self.grade_subject = None

  def resolve(self):
    try:
      school, year, semester = itemgetter('school', 'year', 'semester')(self.school_info)

      self.school_year = SchoolYearInfo.query.filter_by(
        school_name=school, year=year, semester=semester
      ).first()
      if not self.school_year:
        return error_response("SchoolYearInfo not found.")

      self.school_grade = SchoolGrade.query.filter_by(
        school_year_id=self.school_year.id, grade=self.grade_str
      ).first()
      if not self.school_grade:
        return error_response("SchoolGrade not found.")

      try:
        enum_subject = SubjectEnum(self.subject_str)
      except ValueError:
        return error_response(f"Invalid subject value: {self.subject_str}")

      self.grade_subject = GradeSubject.query.filter_by(
        grade_id=self.school_grade.id, subject=enum_subject
      ).first()

      if not self.grade_subject:
        return error_response("GradeSubject not found.")

    except Exception as e:
      return error_response("학교 정보 처리 중 오류가 발생했습니다.", 500, str(e))

    return None  # 성공 시 None 반환


@bp.route("/get_achievement_criteria_by_field_id/<string:fid>", methods=['GET'])
@login_required
def get_achievement_criteria_by_field_id(fid):
  try:
    try:
      area_id = int(fid)
    except ValueError:
      return jsonify({"error": "유효하지 않은 분야 ID입니다."}), 400

    area = AssessmentArea.query.get(area_id)
    if not area:
      return jsonify({"error": "해당 분야가 존재하지 않습니다."}), 404

    criteria = AchievementCriterion.query.filter_by(area_id=area_id).all()

    result = [
      {
        "criterion": c.criterion,
        "evaluation_item": c.evaluation_item,
        "id": c.id
      }
      for c in criteria
    ]

    return jsonify({
      "message": "성취기준 조회 완료",
      "criteria": result
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))
  

# 성취기준 조회 엔드포인트
@bp.route("/get_achievement_criteria_by_grade_subject_area/", methods=['GET'])
@login_required
def get_achievement_criteria_by_grade_subject_area():
  grade = request.args.get('grade')
  subject = request.args.get('subject')
  field = request.args.get('field')

  if not (grade and subject and field):
    return error_response("Missing required parameters.", 400, {
      "grade": grade,
      "subject": subject,
      "field": field
    })

  school_info = session.get('active_school_info')
  if not school_info:
    return error_response("활성화된 학교 정보가 없습니다.")

  # 공통 학사 정보 조회
  context = SchoolContext(school_info, grade, subject)
  err = context.resolve()
  if err:
    return err

  try:
    # 영역 조회 (selectinload로 N+1 회피)
    if field == '-전체-':
      areas = AssessmentArea.query.options(
        selectinload(AssessmentArea.criteria)
      ).filter_by(subject_id=context.grade_subject.id).all()
    else:
      areas = AssessmentArea.query.options(
        selectinload(AssessmentArea.criteria)
      ).filter_by(subject_id=context.grade_subject.id, area=field).all()

    # 성취기준 정리
    result = defaultdict(list)
    for area in areas:
      for criterion in area.criteria:
        result[area.area].append([
          criterion.criterion,
          criterion.evaluation_item,
          criterion.is_assessed,
          criterion.is_observed
        ])

    return jsonify({
      "message": "성취기준 조회 완료",
      "criteria": result
    }), 200

  except Exception as e:
    print(f"[ERROR] 성취기준 조회 실패: {e}")
    return error_response("서버 오류 발생", 500, str(e))
  

@bp.route("/update_achievement_criteria_records/", methods=['POST'])
@login_required
def update_achievement_criteria_records():
  try:
    data = request.get_json()
    basic_info = data.get('basicInfo')
    if not basic_info:
      return jsonify({"error": "기본 정보 누락"}), 400

    grade = basic_info['grade']
    subject_label = basic_info['subject']

    school_info = session.get('active_school_info')
    if not school_info:
      return jsonify({"error": "활성화된 학교 정보가 없습니다."}), 400

    context = SchoolContext(school_info, grade, subject_label)
    err = context.resolve()
    if err:
      return err

    try:
      subject_enum = SubjectEnum(subject_label)
    except ValueError:
      return jsonify({"error": "올바르지 않은 교과명입니다."}), 400

    grade_subject = GradeSubject.query.filter_by(
      id=context.grade_subject.id, subject=subject_enum
    ).first()

    if not grade_subject:
      return jsonify({"error": "해당 교과 정보가 없습니다."}), 404

    # 클라이언트로부터 온 분야별 성취기준 처리
    for area in grade_subject.assessment_areas:
      area_data = data.get(area.area, {})
      if not isinstance(area_data, dict) or not area_data:
        continue

      existing_criteria = area.criteria
      matched_keys = set(area_data.keys())
      max_existing_index = 0

      for idx, criterion in enumerate(existing_criteria, start=1):
        str_idx = str(idx)
        if str_idx in area_data:
          crit, eval_item, sort_order = area_data[str_idx]
          criterion.criterion = crit
          criterion.evaluation_item = eval_item
          criterion.sort_order = int(sort_order)
          max_existing_index = idx
        else:
          db.session.delete(criterion)

      #새 항목 추가
      next_idx = max_existing_index + 1
      while str(next_idx) in area_data:
        crit, eval_item, sort_order = area_data[str(next_idx)]
        db.session.add(AchievementCriterion(
          area_id=area.id,
          criterion=crit,
          evaluation_item=eval_item,
          sort_order = int(sort_order) if str(sort_order).strip().isdigit() else 100
        ))
        next_idx += 1

    db.session.commit()
    return jsonify({"success": True}), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({"error": "Server error", "details": str(e)}), 500
  

@bp.route("/get_evaluation_criteria_by_achievement_criterion/", methods=['POST'])
@login_required
def get_evaluation_criteria_by_achievement_criterion():
  data = request.get_json()
  if not data:
    return jsonify({"error": "Invalid request."}), 400

  _grade, _subject, _field, _criterion, _evaluation = itemgetter(
    'grade', 'subject', 'field', 'criterion', 'evaluation'
  )(data)

  if not (_grade and _subject and _field and _criterion and _evaluation):
    return error_response("Missing required parameters.", 400, {
      "grade": _grade,
      "subject": _subject,
      "field": _field,
      "criterion": _criterion,
      "evaluation": _evaluation
    })

  school_info = session.get('active_school_info')
  if not school_info:
    return error_response("활성화된 학교 정보가 없습니다.")

  context = SchoolContext(school_info, _grade, _subject)
  err = context.resolve()
  if err:
    return err

  try:
    area = AssessmentArea.query.filter_by(
      subject_id=context.grade_subject.id, area=_field
    ).first()
    if not area:
      return jsonify({"error": "AssessmentArea not found."}), 404

    criterion = AchievementCriterion.query.filter_by(
      area_id=area.id, criterion=_criterion, evaluation_item=_evaluation
    ).first()
    if not criterion:
      return jsonify({"error": "AchievementCriterion not found."}), 404

    records = EvaluationCriteria.query.filter_by(criterion_id=criterion.id).order_by(EvaluationCriteria.step).all()
    result = [[r.level_name, r.description] for r in records]

    return jsonify({
      "message": "평가기준 조회 완료",
      "criterion_id": criterion.id,
      "moddable": criterion.is_assessed or criterion.is_observed,
      "result": result,
    }), 200

  except Exception as e:
    print(f"[ERROR] 평가기준 조회 실패: {e}")
    return error_response("서버 오류 발생", 500, str(e))


@bp.route("/manage_evaluation_criteria_records/", methods=['POST'])
@login_required
def manage_evaluation_criteria_records():
  try:
    data = request.get_json()
    if not data or not isinstance(data, dict):
      return jsonify({"error": "데이터 형식이 잘못되었습니다."}), 400

    criterion_id_raw = data.get('criterionId')
    if not criterion_id_raw:
      return jsonify({"error": "기본 정보 누락: criterionId"}), 400

    try:
      criterion_id = int(criterion_id_raw)
    except ValueError:
      return jsonify({"error": "criterionId는 정수여야 합니다."}), 400

    del data['criterionId']

    existing = EvaluationCriteria.query.filter_by(criterion_id=criterion_id).all()
    existing_dict = {c.step: c for c in existing}

    incoming_steps = {int(k): v for k, v in data.items()}

    # 업데이트 또는 생성
    for step, [level_name, description] in incoming_steps.items():
      if step in existing_dict:
        existing_dict[step].level_name = level_name
        existing_dict[step].description = description
      else:
        db.session.add(EvaluationCriteria(
          criterion_id=criterion_id,
          step=step,
          level_name=level_name,
          description=description
        ))

    # 삭제할 항목 제거
    incoming_step_set = set(incoming_steps.keys())
    for step, obj in existing_dict.items():
      if step not in incoming_step_set:
        db.session.delete(obj)

    db.session.commit()

    records = EvaluationCriteria.query.filter_by(criterion_id=criterion_id).order_by(EvaluationCriteria.step).all()
    result = [[r.level_name, r.description] for r in records]

    return jsonify({
      "message": "평가기준 저장 완료",
      "result": result,
      "saved": len(incoming_steps),
      "deleted": len([s for s in existing_dict if s not in incoming_step_set])
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

# 성취기준별 특정 학급 학생들의 성적 조회 엔드포인트
@bp.route("/get_students_achievement_results/", methods=['GET'])
@login_required
def get_students_achievement_results():
  class_id = request.args.get('class_id')
  achievement_id = request.args.get('achievement_id')

  if not (class_id and achievement_id):
    return error_response("Missing required parameters.", 400, {
      "class_id": class_id,
      "achievement_id": achievement_id
    })

  try:
    class_id = int(class_id)
    achievement_id = int(achievement_id)
  except ValueError:
    return error_response("class_id 및 achievement_id는 정수여야 합니다.", 400)

  try:
    gc = GradeClass.query.options(
      selectinload(GradeClass.students)
        .selectinload(Student.evaluation_results)
          .selectinload(EvaluationResult.evidences)
    ).filter_by(id=class_id).first()

    if not gc:
      return error_response("해당 학급을 찾을 수 없습니다.", 404)

    result = []
    for student in gc.students:
      if not student.is_enrolled:
        continue

      er = next(
        (r for r in student.evaluation_results if r.achievement_criterion_id == achievement_id),
        None
      )

      result.append({
        "student_id": student.id,
        "name": student.name,
        "achievement_id": achievement_id,
        "level": er.level if er else None,
        "description": er.description if er else None,
        "evidence_count": len(er.evidences) if er else 0
      })

    return jsonify({
      "message": "학급 학생 및 성적 조회 완료",
      "info": result
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))
  

@bp.route("/get_evaluation_criteria_by_achievement_id/<string:aid>", methods=['GET'])
@login_required
def get_evaluation_criteria_by_achievement_id(aid):
  try:
    try:
      achievement_id = int(aid)
    except ValueError:
      return jsonify({"error": "유효하지 않은 성취기준 ID입니다."}), 400

    ac = AchievementCriterion.query.options(
      selectinload(AchievementCriterion.evaluation_criteria)
    ).filter_by(id=achievement_id).first()

    if not ac:
      return jsonify({"error": "해당 성취기준이 존재하지 않습니다."}), 404

    result = [
      {
        "step": c.step,
        "level_name": c.level_name,
        "description": c.description
      }
      for c in ac.evaluation_criteria
    ]

    return jsonify({
      "message": "평가기준 조회 완료",
      "criteria": result
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))


@bp.route("/update_student_achievement_levels/", methods=['POST'])
@login_required
def update_student_achievement_levels():
  try:
    data = request.get_json()
    if not data or not isinstance(data, list):
      return jsonify({"error": "데이터 형식이 잘못되었습니다. 리스트 형태여야 합니다."}), 400

    created, updated = 0, 0

    for row in data:
      sid, aid, lv, desc = itemgetter(
        'student_id', 'achievement_id', 'level', 'description'
      )(row)

      sid = int(sid)
      aid = int(aid)

      er = EvaluationResult.query.filter_by(
        student_id=sid,
        achievement_criterion_id=aid
      ).first()

      if er:
        if er.level != lv or er.description != desc:
          er.level = lv
          er.description = desc
          updated += 1
      else:
        er = EvaluationResult(
          student_id = sid,
          achievement_criterion_id = aid,
          level = lv,
          description = desc
        )
        db.session.add(er)
        created += 1
    
    ac = AchievementCriterion.query.filter_by(id = aid).first()
    if ac and not ac.is_assessed:
      ac.is_assessed = True
    
    db.session.commit()

    return jsonify({
      "message": "평가결과 저장 완료",
      "created": created,
      "updated": updated,
      "total": created + updated
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/create_evaluation_evidence/", methods=['POST'])
@login_required
def create_evaluation_evidence():
  try:
    data = request.get_json()
    if not data or not isinstance(data, dict):
      return jsonify({"error": "데이터 형식이 잘못되었습니다."}), 400

    # 안전하게 필드 추출
    try:
      sid_raw, aid_raw, file_path, file_ext = itemgetter(
        'student_id', 'achievement_id', 'resource_path', 'ext'
      )(data)
    except KeyError:
      return jsonify({"error": "필수 항목이 누락되었습니다."}), 400

    # 정수 변환
    try:
      sid = int(sid_raw)
      aid = int(aid_raw)
    except ValueError:
      return jsonify({"error": "sid와 aid는 정수여야 합니다."}), 400

    # 확장자 → 타입 분류
    file_type = None
    media_types = {
      'image': ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'tiff', 'webp'],
      'video': ['mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'],
      'audio': ['mp3', 'aac', 'm4a', 'ogg', 'wav', 'flac', 'webm'],
      'doc': ['hwp', 'hwpx', 'pdf', 'ppt', 'pptx']
    }

    ext = file_ext.lower()
    for k in media_types:
      if ext in media_types[k]:
        file_type = k
        break

    if file_type is None:
      return jsonify({"error": "지원되지 않는 파일 형식입니다."}), 400

    evaluation_result = EvaluationResult.query.filter_by(
      student_id=sid, achievement_criterion_id=aid
    ).first()

    if not evaluation_result:
      return jsonify({"error": "평가결과가 생성되지 않았습니다."}), 404

    evidence = EvaluationEvidence(
      result_id=evaluation_result.id,
      resource_path=file_path,
      resource_type=file_type
    )

    db.session.add(evidence)
    db.session.commit()

    return jsonify({
      "message": "평가근거자료 저장 완료"
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/get_evaluation_evidence_by_student_and_achievement/", methods=['GET'])
@login_required
def get_evaluation_evidence_by_student_and_achievement():
  sid_raw = request.args.get('student_id')
  aid_raw = request.args.get('achievement_id')

  if not (sid_raw and aid_raw):
    return error_response("Missing required parameters.", 400, {
      "student_id": sid_raw,
      "achievement_id": aid_raw
    })

  try:
    sid = int(sid_raw)
    aid = int(aid_raw)
  except ValueError:
    return error_response("student_id 및 achievement_id는 정수여야 합니다.", 400)

  try:
    er = EvaluationResult.query.options(
      selectinload(EvaluationResult.evidences)
    ).filter_by(
      student_id=sid,
      achievement_criterion_id=aid
    ).first()

    # 평가결과가 없거나 평가근거자료가 없는 경우
    if not er or not er.evidences:
      return jsonify({
        "message": "평가근거자료가 없습니다.",
        "info": []
      }), 200

    result = []
    for evidence in sorted(er.evidences, key=lambda e: e.created_at or e.id, reverse=True):
      result.append({
        "evidence_id": evidence.id, 
        "resource_path": evidence.resource_path,
        "resource_type": evidence.resource_type,
        "created_at": evidence.created_at.strftime("%Y.%m.%d.") if evidence.created_at else "",
        "updated_at": evidence.updated_at.strftime("%Y.%m.%d.") if evidence.updated_at else "",
      })

    return jsonify({
      "message": "평가근거자료 조회 완료",
      "info": result
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))
  

@bp.route("/delete_evaluation_evidence_by_id/", methods=['DELETE'])
@login_required
def delete_evaluation_evidence_by_id():
  try:
    data = request.get_json()
    if not isinstance(data, dict):
      return jsonify({"error": "Invalid request format. Expected a JSON object."}), 400

    eid_raw = data.get('eid')
    try:
      eid = int(eid_raw)
    except (TypeError, ValueError):
      return error_response("evaluation_id는 정수여야 합니다.", 400)

    evidence = EvaluationEvidence.query.filter_by(id=eid).first()

    if not evidence:
      return error_response(f"id가 {eid}인 평가근거자료를 찾지 못했습니다.", 404)

    db.session.delete(evidence)
    db.session.commit()

    return jsonify({
      "message": "삭제 완료",
      "deleted_id": eid,
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500


@bp.route("/update_evaluation_evidence_resource/", methods=['PATCH'])
@login_required
def update_evaluation_evidence_resource():
  try:
    data = request.get_json()
    if not isinstance(data, dict):
      return jsonify({"error": "Invalid request format. Expected a JSON object."}), 400

    try:
      eid_raw, resource_path, file_ext = itemgetter(
          'eid', 'resource_path', 'ext')(data)
    except (KeyError, TypeError):
      return jsonify({"error": "필수 항목이 누락되었습니다."}), 400

    try:
      eid = int(eid_raw)
    except (TypeError, ValueError):
      return error_response("evaluation_id는 정수여야 합니다.", 400)

    ext = file_ext.lower()
    file_type = None
    media_types = {
      'image': ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'tiff', 'webp'],
      'video': ['mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'],
      'audio': ['mp3', 'aac', 'm4a', 'ogg', 'wav', 'flac', 'webm'],
      'doc': ['hwp', 'hwpx', 'pdf', 'ppt', 'pptx']
    }

    for k in media_types:
      if ext in media_types[k]:
        file_type = k
        break

    if file_type is None:
      return jsonify({"error": "지원되지 않는 파일 형식입니다."}), 400

    evidence = EvaluationEvidence.query.filter_by(id=eid).first()
    if not evidence:
      return error_response(f"id가 {eid}인 평가근거자료를 찾을 수 없습니다.", 404)

    evidence.resource_path = resource_path
    evidence.resource_type = file_type

    db.session.commit()

    return jsonify({
      "message": "평가근거자료 수정 완료",
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/get_grade_class_students_assessment_areas_and_subject_id/", methods=['GET'])
@login_required
def get_grade_class_students_assessment_areas_and_subject_id():
  grade_str = request.args.get('grade')
  class_name = request.args.get('class')  # 실제로는 'class_name'으로 받는 게 더 낫습니다
  subject_str = request.args.get('subject')

  if not (grade_str and class_name and subject_str):
    return error_response("필수 파라미터가 누락되었습니다.", 400, {
      "grade": grade_str,
      "class": class_name,
      "subject": subject_str
    })

  school_info = session.get('active_school_info')
  if not school_info:
    return error_response("활성화된 학교 정보가 없습니다.", 401)

  context = SchoolContext(school_info, grade_str, subject_str)
  err = context.resolve()
  if err:
    return err  # 혹은 예외로 처리해도 좋음

  try:
    gc = GradeClass.query.options(
      selectinload(GradeClass.students)
    ).filter_by(
      school_grade_id=context.school_grade.id,
      class_name=class_name
    ).first()

    if not gc or not gc.students:
      return jsonify({
        "message": "등록된 학급 또는 소속된 학생이 없습니다.",
        "info": []
      }), 200

    student_info = [[student.id, student.name] for student in sorted(gc.students, key=lambda s: s.name)]
    fields_info = [[field.id, field.area] for field in context.grade_subject.assessment_areas]
    
    return jsonify({
      "message": "관찰기록 기본자료 조회 완료",
      "student_info": student_info,
      "fields_info": fields_info,
      "subject_id": context.grade_subject.id
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))
  

@bp.route("/getStudentObservationsBySubject/", methods=['GET'])
@login_required
def getStudentObservationsBySubject():
  sid = request.args.get('student_id')
  gsid = request.args.get('grade_subject_id')
  main_option = request.args.get('main_select')
  sub_option = request.args.get('sub_select')  # 문자열로 들어옴 (예: '0')
  # print("sub_option: ", sub_option)
  # print("sub_option_type: ", type(sub_option))

  if not (sid and gsid):
    return error_response("필수 파라미터가 누락되었습니다.", 400, {
      "student_id": sid,
      "grade_subject_id": gsid
    })

  try:
    sid = int(sid)
    gsid = int(gsid)
  except ValueError:
    return error_response("ID는 정수여야 합니다.", 400)

  try:
    query = Observation.query.filter_by(student_id=sid, grade_subject_id=gsid)

    if main_option != '전체':
      try:
        classification_enum = ObservationClassification(main_option)
        query = query.filter_by(classification=classification_enum)
      except ValueError:
        return error_response(f"'{main_option}'은 유효하지 않은 분류 항목입니다.", 400)

      if classification_enum == ObservationClassification.evaluation and sub_option != '0':
        query = query.filter_by(area_id=int(sub_option))

    observations = query.order_by(Observation.created_at.asc()).all()

    obs_info = [
      {
        'oid': ob.id,
        'desc': ob.description,
        'category': ob.classification.value,
        'aid': ob.area_id,
        'created_at': ob.created_at.strftime("%Y-%m-%d") if ob.created_at else None,
        'evidence_count': len(ob.evidences) if ob.evidences else 0
      }
      for ob in observations
    ]

    return jsonify({
      "message": "학생 관찰기록 조회 완료",
      "observation_info": obs_info
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))
  

@bp.route("/manageObservationsBatch/", methods=['POST'])
@login_required
def manageObservationsBatch():
  try:
    data = request.get_json()
    # if not data or not isinstance(data, list):
    #   return jsonify({"error": "데이터 형식이 잘못되었습니다. 리스트가 필요합니다."}), 400

    sid = request.args.get('student_id')
    gsid = request.args.get('grade_subject_id')
    main = request.args.get('main_select')
    sub = request.args.get('sub_select')
     
    existing_obs = None

    if not (sid and gsid and main and sub):
      return error_response("필수 파라미터가 누락되었습니다.", 400, {
        "student_id": sid,
        "grade_subject_id": gsid,
        "main_select": main,
        "sub_select": sub
      })

    try:
      sid = int(sid)
      gsid = int(gsid)
      sub = 0 if sub == '전체' else int(sub)
    except ValueError:
      return error_response("ID는 정수여야 합니다.", 400)

    # 기존 관찰기록을 dict 형태로 가져오기 (id → Observation 객체)

    if main == '전체':
      existing_obs = {
        ob.id: ob for ob in Observation.query.filter_by(student_id=sid, grade_subject_id=gsid).all()
      }
    elif main == '평가내용' and sub != 0:
      existing_obs = {
        ob.id: ob for ob in Observation.query.filter_by(student_id=sid, grade_subject_id=gsid, classification=ObservationClassification(main), area_id=sub).all()
      }
    else:
      existing_obs = {
        ob.id: ob for ob in Observation.query.filter_by(student_id=sid, grade_subject_id=gsid, classification=ObservationClassification(main)).all()
      }
      
    received_ids = set()

    for item in data:
      try:
        oid_raw, created_str, desc, classification, area_id_raw = itemgetter(
          'id', 'created_at', 'desc', 'classification', 'area_id'
        )(item)
      except KeyError:
        return jsonify({"error": "필수 항목이 누락되었습니다."}), 400

      # 변환
      try:
        oid = int(oid_raw)
        area_id = int(area_id_raw) if area_id_raw not in (None, '', '0') else None
        class_enum = ObservationClassification(classification)
        created_at = datetime.strptime(created_str, '%Y-%m-%d')
      except (ValueError, TypeError) as e:
        return jsonify({"error": "형식 오류: id, area_id는 정수, created_at은 'YYYY-MM-DD' 형식, classification은 올바른 값이어야 합니다."}), 400

      if oid == 0:
        # 새 레코드 생성
        new_obs = Observation(
          student_id=sid,
          grade_subject_id=gsid,
          description=desc,
          classification=class_enum,
          area_id=area_id,
          created_at=created_at
        )
        db.session.add(new_obs)
      elif oid in existing_obs:
        # 기존 항목 업데이트
        obs_item = existing_obs[oid]
        obs_item.description = desc
        obs_item.classification = class_enum
        obs_item.area_id = area_id
        obs_item.created_at = created_at
        received_ids.add(oid)

    # 누락된 기존 레코드 삭제 (클라이언트가 보내지 않은 것)
    for oid, obs_item in existing_obs.items():
      if oid not in received_ids:
        db.session.delete(obs_item)

    db.session.commit()

    return jsonify({
      "message": "관찰내용 저장 및 수정 완료"
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/get_observation_evidence_by_observation_id/", methods=['GET'])
@login_required
def get_observation_evidence_by_observation_id():
  oid_raw = request.args.get('observation_id')

  if not oid_raw:
    return error_response("Missing required parameters.", 400, {
      "observation_id": oid_raw
    })

  try:
    oid = int(oid_raw)
  except ValueError:
    return error_response("observation_id는 정수여야 합니다.", 400)

  try:
    obs = Observation.query.options(
      selectinload(Observation.evidences)
    ).filter_by(
      id=oid
    ).first()

    # 관찰내용이 없거나 관찰근거자료가 없는 경우
    if not obs or not obs.evidences:
      return jsonify({
        "message": "관찰근거자료가 없습니다.",
        "info": []
      }), 200

    result = []
    for evidence in sorted(obs.evidences, key=lambda e: e.created_at or e.id, reverse=True):
      result.append({
        "evidence_id": evidence.id, 
        "resource_path": evidence.resource_path,
        "resource_type": evidence.resource_type,
        "created_at": evidence.created_at.strftime("%Y.%m.%d.") if evidence.created_at else "",
        "updated_at": evidence.updated_at.strftime("%Y.%m.%d.") if evidence.updated_at else "",
        "evidence_count": True if evidence else False,
      })

    return jsonify({
      "message": "관찰근거자료 조회 완료",
      "info": result
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))
  

@bp.route("/create_observation_evidence/", methods=['POST'])
@login_required
def create_observation_evidence():
  try:
    data = request.get_json()
    if not data or not isinstance(data, dict):
      return jsonify({"error": "데이터 형식이 잘못되었습니다."}), 400

    # 안전하게 필드 추출
    try:
      oid_raw, file_path, file_ext = itemgetter(
        'observation_id', 'resource_path', 'ext'
      )(data)
    except KeyError:
      return jsonify({"error": "필수 항목이 누락되었습니다."}), 400

    # 정수 변환
    try:
      oid = int(oid_raw)
    except ValueError:
      return jsonify({"error": "oid는 정수여야 합니다."}), 400

    # 확장자 → 타입 분류
    file_type = None
    media_types = {
      'image': ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'tiff', 'webp'],
      'video': ['mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'],
      'audio': ['mp3', 'aac', 'm4a', 'ogg', 'wav', 'flac', 'webm'],
      'doc': ['hwp', 'hwpx', 'pdf', 'ppt', 'pptx']
    }

    ext = file_ext.lower()
    for k in media_types:
      if ext in media_types[k]:
        file_type = k
        break

    if file_type is None:
      return jsonify({"error": "지원되지 않는 파일 형식입니다."}), 400

    obs = Observation.query.filter_by(id=oid).first()

    if not obs:
      return jsonify({"error": "관찰내용이 생성되지 않았습니다."}), 404

    evidence = ObservationEvidence(
      observation_id=obs.id,
      resource_path=file_path,
      resource_type=file_type
    )

    db.session.add(evidence)
    db.session.commit()

    return jsonify({
      "message": "관찰근거자료 저장 완료"
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/delete_observation_evidence_by_id/", methods=['DELETE'])
@login_required
def delete_observation_evidence_by_id():
  try:
    data = request.get_json()
    if not isinstance(data, dict):
      return jsonify({"error": "Invalid request format. Expected a JSON object."}), 400

    eid_raw = data.get('eid')
    try:
      eid = int(eid_raw)
    except (TypeError, ValueError):
      return error_response("evaluation_id는 정수여야 합니다.", 400)

    evidence = ObservationEvidence.query.filter_by(id=eid).first()

    if not evidence:
      return error_response(f"id가 {eid}인 관찰근거자료를 찾지 못했습니다.", 404)

    db.session.delete(evidence)
    db.session.commit()

    return jsonify({
      "message": "삭제 완료",
      "deleted_id": eid,
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/update_observation_evidence_resource/", methods=['PATCH'])
@login_required
def update_observation_evidence_resource():
  try:
    data = request.get_json()
    if not isinstance(data, dict):
      return jsonify({"error": "Invalid request format. Expected a JSON object."}), 400

    try:
      eid_raw, resource_path, file_ext = itemgetter(
          'eid', 'resource_path', 'ext')(data)
    except (KeyError, TypeError):
      return jsonify({"error": "필수 항목이 누락되었습니다."}), 400

    try:
      eid = int(eid_raw)
    except (TypeError, ValueError):
      return error_response("evaluation_id는 정수여야 합니다.", 400)

    ext = file_ext.lower()
    file_type = None
    media_types = {
      'image': ['png', 'jpeg', 'jpg', 'gif', 'bmp', 'tiff', 'webp'],
      'video': ['mp4', 'avi', 'mov', 'mkv', 'wmv', 'flv'],
      'audio': ['mp3', 'aac', 'm4a', 'ogg', 'wav', 'flac', 'webm'],
      'doc': ['hwp', 'hwpx', 'pdf', 'ppt', 'pptx']
    }

    for k in media_types:
      if ext in media_types[k]:
        file_type = k
        break

    if file_type is None:
      return jsonify({"error": "지원되지 않는 파일 형식입니다."}), 400

    evidence = ObservationEvidence.query.filter_by(id=eid).first()
    if not evidence:
      return error_response(f"id가 {eid}인 관찰근거자료를 찾을 수 없습니다.", 404)

    evidence.resource_path = resource_path
    evidence.resource_type = file_type

    db.session.commit()

    return jsonify({
      "message": "관찰근거자료 수정 완료",
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500
  

@bp.route("/manageObservationsBatchBySids/", methods=['POST'])
@login_required
def manageObservationsBatchBySids():
  try:
    data = request.get_json()
    if not data or not isinstance(data, list):
      return jsonify({"error": "데이터 형식이 잘못되었습니다. 리스트가 필요합니다."}), 400

    sids_raw = request.args.get('student_ids')
    gsid_raw = request.args.get('grade_subject_id')

    if not (sids_raw and gsid_raw):
      return error_response("필수 파라미터가 누락되었습니다.", 400, {
        "student_ids": sids_raw,
        "grade_subject_id": gsid_raw
      })

    # student_ids는 '1,2,3' 같은 문자열로 전달됨
    try:
      sids = [int(sid.strip()) for sid in sids_raw.split(',')]
      gsid = int(gsid_raw)
    except ValueError:
      return error_response("ID는 정수여야 합니다.", 400)

    for sid in sids:
      for item in data:
        try:
          created_str, desc, classification, area_id_raw = itemgetter(
            'created_at', 'desc', 'classification', 'area_id'
          )(item)
        except KeyError:
          return jsonify({"error": "필수 항목이 누락되었습니다."}), 400

        try:
          created_at = datetime.strptime(created_str, '%Y-%m-%d')
          area_id = int(area_id_raw) if area_id_raw not in (None, '', '0') else None
          class_enum = ObservationClassification(classification)
        except (ValueError, TypeError):
          return jsonify({
            "error": "형식 오류: area_id는 정수, created_at은 'YYYY-MM-DD' 형식, classification은 지정된 Enum 값이어야 합니다."
          }), 400

        # 중복 방지용 체크 (선택적)
        exists = Observation.query.filter_by(
          student_id=sid,
          grade_subject_id=gsid,
          description=desc,
          classification=class_enum,
          area_id=area_id,
          created_at=created_at
        ).first()

        if exists:
          continue  # 동일한 항목이 있다면 skip

        new_obs = Observation(
          student_id=sid,
          grade_subject_id=gsid,
          description=desc,
          classification=class_enum,
          area_id=area_id,
          created_at=created_at
        )
        db.session.add(new_obs)

    db.session.commit()

    return jsonify({
      "message": "관찰기록 일괄입력 저장 완료"
    }), 200

  except Exception as e:
    db.session.rollback()
    print(traceback.format_exc())
    return jsonify({
      "error": "서버 오류 발생",
      "details": str(e)
    }), 500

    
@bp.route("/get_image_resource_paths_by_student_and_subjectId/", methods=['GET'])
@login_required
def get_image_resource_paths_by_student_and_subjectId():
  sid_raw = request.args.get('student_id')
  gsid_raw = request.args.get('grade_subject_id')

  if not (sid_raw and gsid_raw):
    return error_response("필수 파라미터가 누락되었습니다.", 400, {
        "student_id": sid_raw,
        "grade_subject_id": gsid_raw
    })

  try:
    sid = int(sid_raw)
    gsid = int(gsid_raw)
  except ValueError:
    return error_response("student_id와 grade_subject_id는 정수여야 합니다.", 400)

  try:
    obss = Observation.query.options(
      selectinload(Observation.evidences)
    ).filter_by(
      student_id=sid,
      grade_subject_id=gsid
    ).all()

    # 관찰내용이 없거나 evidence가 전부 비어있는 경우
    if not obss or not any(obs.evidences for obs in obss):
      return jsonify({
        "message": "관찰근거자료가 없습니다.",
        "info": []
      }), 200

    result = []

    for obs in obss:
      for evidence in sorted(obs.evidences, key=lambda e: e.created_at or e.id):
        if evidence.resource_type != 'image':
          continue
        result.append(evidence.resource_path)

    return jsonify({
      "message": "관찰근거자료 조회 완료",
      "info": result
    }), 200

  except Exception as e:
    print(traceback.format_exc())
    return error_response("서버 오류 발생", 500, str(e))