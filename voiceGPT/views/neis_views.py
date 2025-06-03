from flask import Blueprint, jsonify, url_for, render_template, flash, request, g, current_app, send_from_directory, abort, send_file, session
from werkzeug.utils import redirect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy import func
from operator import itemgetter 
import os
import json
import configparser
import unicodedata
import glob
import csv
import pprint
import io
import base64
from .. import db
from dotenv import load_dotenv
from .auth_views import login_required
from ..models import SchoolYearInfo, SchoolGrade, SubjectEnum, GradeSubject, GradeClass, Student, student_class_association
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
    str(root_dir), "NEIS", school, year, f"{semester}학기"
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
        str(root_dir), "NEIS", school, str(year), f"{semester}학기"
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