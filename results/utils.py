import pandas as pd
import logging
from .models import Student, Result, Backlog

logger = logging.getLogger(__name__)

def calculate_si(passed, total):
    """Calculate Success Index (Format 12)."""
    if total == 0:
        return 0.0
    return round(passed / total, 2)

def calculate_api(mean_sgpa, passed, appeared):
    """Calculate Academic Performance Index (Format 12)."""
    si = calculate_si(passed, appeared)
    return round(mean_sgpa * si, 2)

def process_uploaded_file(uploaded_file, user):
    """
    Parse CSV/Excel files, validate columns, and load into models.
    """
    filename = uploaded_file.name.lower()
    error_messages = []
    count = 0
    
    try:
        # Support multiple sheets for Excel files
        if filename.endswith('.csv'):
            dfs = {'Sheet1': pd.read_csv(uploaded_file)}
        elif filename.endswith(('.xlsx', '.xls')):
            dfs = pd.read_excel(uploaded_file, sheet_name=None)
        else:
            return 0, [f"{filename}: Unsupported format. Please upload CSV or Excel."]

        # Delete existing data for this file once before processing all sheets
        Student.objects.filter(source_file=uploaded_file.name).delete()

        for sheet_name, df in dfs.items():

            # Smart header detection
            header_idx = -1
            for idx, row in df.head(20).iterrows():
                row_strs = [str(val).lower() for val in row.values]
                if any('name' in val for val in row_strs):
                    header_idx = idx
                    break
            
            if header_idx > -1:
                df.columns = df.iloc[header_idx]
                df = df.iloc[header_idx + 1:].reset_index(drop=True)

            original_cols = [str(c).strip() for c in df.columns]
            lower_cols = [c.lower() for c in original_cols]
            df.columns = original_cols

            # Detect meta columns
            name_idx     = next((i for i, c in enumerate(lower_cols) if 'name' in c), None)
            usn_idx      = next((i for i, c in enumerate(lower_cols) if 'usn' in c or 'roll' in c), None)
            sgpa_idx     = next((i for i, c in enumerate(lower_cols) if 'sgpa' in c or 'gpa' in c), None)
            sem_idx      = next((i for i, c in enumerate(lower_cols) if 'sem' in c and not 'semester' in c), None)
            gender_idx   = next((i for i, c in enumerate(lower_cols) if 'gender' in c), None)
            category_idx = next((i for i, c in enumerate(lower_cols) if 'category' in c or 'caste' in c), None)
            quota_idx    = next((i for i, c in enumerate(lower_cols) if 'quota' in c or 'admission type' in c), None)
            
            # Extended detection to prevent parsing metadata as subjects
            rank_idx     = next((i for i, c in enumerate(lower_cols) if c == 'rank' or c == 'sl.no' or c == 'sl no'), None)
            total_idx    = next((i for i, c in enumerate(lower_cols) if c in ('total', 'total marks', 'grand total')), None)
            result_idx   = next((i for i, c in enumerate(lower_cols) if c in ('result', 'grade', 'status', 'remarks', 'pass/fail')), None)
            sub_code_idx = next((i for i, c in enumerate(lower_cols) if 'subject code' in c or 'course code' in c), None)
            sub_name_idx = next((i for i, c in enumerate(lower_cols) if 'subject name' in c or 'course name' in c), None)
            batch_idx    = next((i for i, c in enumerate(lower_cols) if 'batch' in c or 'year' in c), None)

            if name_idx is None:
                continue # Skip sheets that don't look like student data

            name_col     = original_cols[name_idx]
            usn_col      = original_cols[usn_idx]      if usn_idx      is not None else None
            sgpa_col     = original_cols[sgpa_idx]     if sgpa_idx     is not None else None
            sem_col      = original_cols[sem_idx]      if sem_idx      is not None else None
            gender_col   = original_cols[gender_idx]   if gender_idx   is not None else None
            category_col = original_cols[category_idx] if category_idx is not None else None
            quota_col    = original_cols[quota_idx]    if quota_idx    is not None else None

            marks_idx = next((i for i, c in enumerate(lower_cols) if c == 'marks' or c == 'score'), None)
            is_vertical = (sub_code_idx is not None or sub_name_idx is not None) and marks_idx is not None

            # Subject columns = all columns that are NOT meta columns (only used in horizontal format)
            meta_indices = {name_idx, usn_idx, sgpa_idx, sem_idx, gender_idx,
                            category_idx, quota_idx, rank_idx, total_idx, result_idx, sub_code_idx, sub_name_idx, marks_idx, batch_idx}
            subject_cols = [original_cols[i] for i in range(len(original_cols)) if i not in meta_indices and original_cols[i]]

            for idx, row in df.iterrows():
                student_name = str(row.get(name_col, '')).strip()
                if not student_name or student_name.lower() == 'nan':
                    continue

                gender   = str(row.get(gender_col, '')).strip()   if gender_col   else 'Unknown'
                category = str(row.get(category_col, '')).strip() if category_col else 'Unknown'
                quota    = str(row.get(quota_col, '')).strip()    if quota_col    else 'Unknown'

                if gender.lower() == 'nan': gender = 'Unknown'
                if category.lower() == 'nan': category = 'Unknown'
                if quota.lower() == 'nan': quota = 'Unknown'

                usn = str(row.get(usn_col, '')).strip() if usn_col else f"NO-USN-{idx}-{sheet_name}"
                semester = str(row.get(sem_col, '1')).strip() if sem_col else '1'
                
                try:
                    sgpa = float(row.get(sgpa_col, 0.0)) if sgpa_col else 0.0
                    if pd.isna(sgpa): sgpa = 0.0
                except (ValueError, TypeError):
                    sgpa = 0.0

                # Use update_or_create to prevent duplicates in vertical format
                student, created = Student.objects.update_or_create(
                    usn=usn,
                    source_file=uploaded_file.name,
                    defaults={
                        'name': student_name,
                        'semester': semester,
                        'sgpa': sgpa,
                        'gender': gender,
                        'category': category,
                        'quota': quota,
                        'uploaded_by': user
                    }
                )
                if created:
                    count += 1

                if is_vertical:
                    subj_col = original_cols[sub_name_idx] if sub_name_idx is not None else original_cols[sub_code_idx]
                    marks_col = original_cols[marks_idx]
                    
                    raw_subj = str(row.get(subj_col, '')).strip()
                    if not raw_subj or raw_subj.lower() == 'nan':
                        continue
                        
                    subject_name = raw_subj.split('-')[0].strip()
                    raw_val = row.get(marks_col, '')
                    
                    is_empty = pd.isna(raw_val) or str(raw_val).strip() == ''
                    try:
                        marks = float(raw_val)
                        text_val = ''
                    except (ValueError, TypeError):
                        marks = 0.0
                        text_val = str(raw_val).strip()
                    
                    Result.objects.update_or_create(
                        student=student,
                        subject=subject_name,
                        defaults={'marks': marks, 'text_value': text_val}
                    )
                    
                    if not is_empty and text_val == '' and marks < 35:
                        Backlog.objects.update_or_create(
                            student=student,
                            subject=subject_name,
                            semester=student.semester
                        )
                else:
                    for subj in subject_cols:
                        raw_val = row[subj]
                        if pd.isna(raw_val):
                            raw_val = ''
                            
                        is_empty = pd.isna(raw_val) or str(raw_val).strip() == ''
                        try:
                            marks = float(raw_val)
                            text_val = ''
                        except (ValueError, TypeError):
                            marks = 0.0
                            text_val = str(raw_val).strip()
                            
                        subject_name = str(subj).split('-')[0].strip()
                        Result.objects.update_or_create(
                            student=student,
                            subject=subject_name,
                            defaults={'marks': marks, 'text_value': text_val}
                        )
                        
                        if not is_empty and text_val == '' and marks < 35:
                            Backlog.objects.update_or_create(
                                student=student,
                                subject=subject_name,
                                semester=student.semester
                            )

            
    except Exception as e:
        logger.error(f"Error processing file {filename}: {str(e)}")
        error_messages.append(f"{filename}: {str(e)}")
        
    return count, error_messages
