import io
import csv
import math
import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Sum, Avg, Count
from django.db import transaction

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .models import Profile, Student, Result, Backlog
from .forms import SignupForm, LoginForm, CSVUploadForm
from .utils import process_uploaded_file, calculate_si, calculate_api


# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────

def get_role(user):
    try:
        return user.profile.role
    except Exception:
        return 'student'


def role_required(*roles):
    """Decorator: only allow users with the given role(s)."""
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('/')
            if get_role(request.user) not in roles:
                messages.error(request, "You don't have permission to access that page.")
                role = get_role(request.user)
                if role == 'student':
                    return redirect('/student/')
                return redirect('/dashboard/')
            return view_func(request, *args, **kwargs)
        wrapper.__name__ = view_func.__name__
        return wrapper
    return decorator


# ─────────────────────────────────────────
#  AUTH VIEWS
# ─────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        role = get_role(request.user)
        return redirect('/student/' if role == 'student' else '/dashboard/')

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            role = get_role(user)
            messages.success(request, f"Welcome back, {user.first_name or user.username}!")
            return redirect('/student/' if role == 'student' else '/dashboard/')
        else:
            messages.error(request, "Invalid username or password.")
    return render(request, 'results/login.html', {'form': form})


def signup_view(request):
    if request.user.is_authenticated:
        return redirect('/')

    form = SignupForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            cd = form.cleaned_data
            if User.objects.filter(username=cd['username']).exists():
                messages.error(request, "Username already taken.")
            else:
                user = User.objects.create_user(
                    username=cd['username'],
                    email=cd.get('email', ''),
                    password=cd['password'],
                    first_name=cd.get('first_name', ''),
                    last_name=cd.get('last_name', ''),
                )
                Profile.objects.create(
                    user=user,
                    role=cd['role'],
                    department=cd.get('department', ''),
                    student_class=cd.get('student_class', ''),
                )
                messages.success(request, "Account created! Please log in.")
                return redirect('/')
        else:
            messages.error(request, "Please fix the errors below.")
    return render(request, 'results/signup.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('/')


# ─────────────────────────────────────────
#  UPLOAD VIEW
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
@transaction.atomic
def upload_view(request):
    form = CSVUploadForm()
    if request.method == 'POST':
        uploaded_files = request.FILES.getlist('file')
        if not uploaded_files:
            return render(request, 'results/upload.html', {'form': form, 'error_msg': 'Please select at least one file.'})
            
        total_count = 0
        all_errors = []
        
        for uploaded_file in uploaded_files:
            count, errors = process_uploaded_file(uploaded_file, request.user)
            total_count += count
            all_errors.extend(errors)

        if total_count > 0:
            messages.success(request, f"Successfully imported {total_count} student records from {len(uploaded_files)} file(s).")
        if all_errors:
            for err in all_errors:
                messages.error(request, err)
        
        if total_count > 0:
            return redirect('/dashboard/')

    return render(request, 'results/upload.html', {'form': form})


# ─────────────────────────────────────────
#  ANALYTICS ENGINE
# ─────────────────────────────────────────

def compute_analytics(selected_file=None, selected_subject=None):
    # ── Base querysets ──
    results_qs = Result.objects.select_related('student').all()

    if selected_file:
        results_qs = results_qs.filter(student__source_file=selected_file)

    if not results_qs.exists():
        return None

    # All subjects from DB (unfiltered) — drives the dropdown list
    all_subjects_raw = sorted(results_qs.values_list('subject', flat=True).distinct())

    # Classify based on STORED VALUES, not column names:
    # If a subject has avg marks > 0  → real numeric marks → full analysis
    # If a subject has avg marks == 0 → text values failed float conversion → graph only
    from django.db.models import Avg
    subject_avg_check = dict(
        results_qs.values('subject').annotate(avg=Avg('marks')).values_list('subject', 'avg')
    )
    numeric_subjects = sorted([s for s, avg in subject_avg_check.items() if avg and float(avg) > 0])
    word_subjects    = sorted([s for s, avg in subject_avg_check.items() if not avg or float(avg) == 0])
    all_subjects = all_subjects_raw

    subject_mode = bool(selected_subject and selected_subject != 'all')
    # A subject is "word-mode" if its average marks are 0 (text attribute)
    is_word_subject = subject_mode and selected_subject in word_subjects

    # Apply ORM-level subject filter (exact DB match, no pandas guessing)
    if subject_mode:
        filtered_qs = results_qs.filter(subject=selected_subject)
    else:
        filtered_qs = results_qs

    if not filtered_qs.exists():
        subject_mode = False
        filtered_qs = results_qs

    # Build DataFrame from the already-filtered queryset
    data = list(filtered_qs.values(
        'student__usn', 'student__name', 'student__gender', 'student__category',
        'student__quota', 'student__source_file', 'student__sgpa', 'subject', 'marks', 'text_value'
    ))
    df = pd.DataFrame(data)
    df.columns = ['usn', 'name', 'gender', 'category', 'quota', 'source_file', 'sgpa', 'subject', 'marks', 'text_value']
    df['marks'] = pd.to_numeric(df['marks'], errors='coerce').fillna(0)
    df['passed'] = df['marks'] >= 35
    df['display_value'] = df.apply(lambda r: r['text_value'] if pd.notnull(r['text_value']) and r['text_value'] != '' else r['marks'], axis=1)

    # Subject averages
    subject_avg = df.groupby('subject')['marks'].mean().round(2).to_dict()

    # Score / Rank Distribution
    max_mark = df['marks'].max() if not df['marks'].empty else 0
    is_rank_data = max_mark > 1000

    # Rank per student based on current view
    student_totals = df.groupby(['name', 'usn']).agg(
        marks=('marks', 'sum'),
        sgpa=('sgpa', 'first')
    ).round(2).reset_index()
    
    if is_rank_data:
        # Find the rank column to sort by exactly its value, bypassing sums of other dirty numeric fields
        rank_col = next((c for c in df['subject'].unique() if 'rank' in c.lower()), None)
        if rank_col:
            rank_df = df[df['subject'] == rank_col][['usn', 'marks']].rename(columns={'marks': 'sort_val'})
            student_totals = student_totals.merge(rank_df, on='usn', how='left')
            student_totals['sort_val'] = student_totals['sort_val'].fillna(0)
            student_totals['sort_val'] = student_totals['sort_val'].apply(lambda x: x if x > 0 else float('inf'))
            student_totals = student_totals.sort_values(by='sort_val', ascending=True).reset_index(drop=True)
            student_totals = student_totals.drop(columns=['sort_val'])
        else:
            student_totals['sort_val'] = student_totals['marks'].apply(lambda x: x if x > 0 else float('inf'))
            student_totals = student_totals.sort_values(by='sort_val', ascending=True).reset_index(drop=True)
            student_totals = student_totals.drop(columns=['sort_val'])
    else:
        if not subject_mode:
            # Overall Top Scorers sorted by SGPA
            student_totals = student_totals.sort_values(by=['sgpa', 'marks'], ascending=[False, False]).reset_index(drop=True)
        else:
            # Subject-specific Top Scorers sorted by Marks
            student_totals = student_totals.sort_values(by=['marks', 'sgpa'], ascending=[False, False]).reset_index(drop=True)
        
    student_totals['rank'] = student_totals.index + 1
    rank_dict = student_totals.set_index('usn')['rank'].to_dict()
    df['rank'] = df['usn'].map(rank_dict)

    top_student = student_totals.iloc[0]['name'] if not student_totals.empty else 'N/A'
    top_score = student_totals.iloc[0]['marks'] if not student_totals.empty else 0
    top_3_list = student_totals.head(3).to_dict('records')
    top_10_list = student_totals.head(10).to_dict('records')

    # Pass / Fail
    pass_count = int(df['passed'].sum())
    fail_count = int((~df['passed']).sum())
    total_results = pass_count + fail_count if subject_mode else len(df)
    pass_pct = round(pass_count / total_results * 100, 2) if total_results else 0

    if not subject_mode:
        student_pass_status = df.groupby('usn')['passed'].all()
        overall_student_pass_count = int(student_pass_status.sum())
        overall_student_fail_count = len(student_pass_status) - overall_student_pass_count
    else:
        overall_student_pass_count = pass_count
        overall_student_fail_count = fail_count

    course_analysis = {}
    if not subject_mode:
        for subj in df['subject'].unique():
            subj_df = df[df['subject'] == subj]
            t_sub = len(subj_df)
            p_sub = int(subj_df['passed'].sum())
            f_sub = t_sub - p_sub
            
            # Buckets based on user image
            d_50_59 = int(((subj_df['marks'] >= 50) & (subj_df['marks'] < 60)).sum())
            d_60_69 = int(((subj_df['marks'] >= 60) & (subj_df['marks'] < 70)).sum())
            d_ge_70 = int((subj_df['marks'] >= 70).sum())
            
            if t_sub > 0:
                course_analysis[str(subj)] = {
                    'pass_pct': round(p_sub / t_sub * 100, 2),
                    'fail_pct': round(f_sub / t_sub * 100, 2),
                    'passed': p_sub,
                    'failed': f_sub,
                    'appeared': t_sub,
                    'd_50_59': d_50_59,
                    'd_60_69': d_60_69,
                    'd_ge_70': d_ge_70
                }

    if is_rank_data:
        score_dist = {
            '< 10k': int((df['marks'] < 10000).sum()),
            '10k - 50k': int(((df['marks'] >= 10000) & (df['marks'] < 50000)).sum()),
            '50k - 100k': int(((df['marks'] >= 50000) & (df['marks'] < 100000)).sum()),
            '> 100k': int((df['marks'] >= 100000).sum()),
        }
    else:
        score_dist = {
            '<35': int((df['marks'] < 35).sum()),
            '35-49': int(((df['marks'] >= 35) & (df['marks'] < 50)).sum()),
            '50-59': int(((df['marks'] >= 50) & (df['marks'] <= 59)).sum()),
            '60-69': int(((df['marks'] >= 60) & (df['marks'] <= 69)).sum()),
            '≥70': int((df['marks'] >= 70).sum()),
        }
        
    # Word Distribution (for text attributes)
    word_dist = {}
    if is_word_subject:
        val_counts = df[df['text_value'] != '']['text_value'].value_counts()
        word_dist = val_counts.to_dict()

    # Category-wise metrics — dynamically use whatever categories exist in the data
    dynamic_cats = sorted(df['category'].dropna().unique().tolist())
    cat_metrics = {}
    for cat in dynamic_cats:
        cat_df = df[df['category'] == cat]
        if not cat_df.empty:
            c_pass = cat_df['passed'].sum()
            c_tot = len(cat_df)
            avg_sgpa = cat_df['sgpa'].mean()
            cat_metrics[cat] = {
                'pass_pct': round(c_pass / c_tot * 100, 1),
                'avg_sgpa': round(avg_sgpa, 2) if pd.notnull(avg_sgpa) else 0.0,
            }

    # NBA-SAR
    si = calculate_si(pass_count, total_results)
    mean_sgpa_val = df['sgpa'].mean()
    mean_sgpa = round(mean_sgpa_val, 2) if pd.notnull(mean_sgpa_val) else 0.0
    api = calculate_api(mean_sgpa, pass_count, total_results)

    lowest_subject = min(subject_avg, key=subject_avg.get) if subject_avg else 'N/A'
    lowest_avg = subject_avg.get(lowest_subject, 0)

    # ── Student detail table ──
    if subject_mode:
        display_subjects = [selected_subject]
        std_df = df[['name', 'usn', 'sgpa', 'rank', 'display_value', 'marks']].copy()
        std_df = std_df.rename(columns={'display_value': 'total'}).sort_values('rank')
        student_detail = std_df.to_dict('records')
        for st in student_detail:
            val = st['total']
            if isinstance(val, (int, float)):
                st['sub_marks'] = [round(float(val), 1)]
            else:
                st['sub_marks'] = [val]
    else:
        display_subjects = list(all_subjects)
        pivot_df = df.pivot_table(index=['usn', 'name'], columns='subject', values='display_value', aggfunc='first').reset_index()
        std_df = df.groupby(['name', 'usn', 'sgpa', 'rank'])['marks'].agg(['sum', 'mean', 'count']).round(2).reset_index()
        std_df.columns = ['name', 'usn', 'sgpa', 'rank', 'total', 'average', 'num_subjects']
        std_df = pd.merge(std_df, pivot_df, on=['usn', 'name'], how='left').sort_values('sgpa', ascending=False)
        student_detail = std_df.to_dict('records')
        for st in student_detail:
            st['sub_marks'] = []
            for sub in display_subjects:
                val = st.get(sub, None)
                if pd.notnull(val):
                    if isinstance(val, (int, float)):
                        if is_rank_data:
                            st['sub_marks'].append(int(val) if val > 0 else 'N/A')
                        else:
                            st['sub_marks'].append(round(float(val), 1))
                    else:
                        st['sub_marks'].append(val)
                else:
                    st['sub_marks'].append('-')

    # Backlogs
    from .models import Backlog
    backlogs_qs = Backlog.objects.all()
    if selected_file:
        backlogs_qs = backlogs_qs.filter(student__source_file=selected_file)
    if subject_mode:
        backlogs_qs = backlogs_qs.filter(subject=selected_subject)
    backlogs_list = list(backlogs_qs.values('student__name', 'student__usn', 'subject', 'semester'))

    return {
        'all_subjects': all_subjects,
        'display_subjects': display_subjects,
        'subject_mode': subject_mode,
        'selected_subject': selected_subject,
        'subject_avg': subject_avg,
        'top_student': top_student,
        'top_score': top_score,
        'top_3': top_3_list,
        'top_10': top_10_list,
        'pass_count': pass_count,
        'fail_count': fail_count,
        'pass_pct': pass_pct,
        'course_analysis': course_analysis,
        'score_dist': score_dist,
        'cat_metrics': cat_metrics,
        'overall_student_pass_count': overall_student_pass_count,
        'overall_student_fail_count': overall_student_fail_count,
        'nba_si': round(si, 2),
        'nba_api': api,
        'mean_sgpa': mean_sgpa,
        'lowest_subject': lowest_subject,
        'lowest_avg': lowest_avg,
        'student_detail': student_detail,
        'backlogs': backlogs_list,
        'total_students': len(student_totals),
        'total_subjects': len(all_subjects),
        'numeric_subjects': numeric_subjects,
        'word_subjects': word_subjects,
        'is_word_subject': is_word_subject,
        'is_rank_data': is_rank_data,
        'word_dist': word_dist,
    }




# ─────────────────────────────────────────
#  DASHBOARD VIEW
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
def dashboard_view(request):
    selected_file = request.GET.get('file')
    if selected_file == 'all': selected_file = None
        
    selected_subject = request.GET.get('subject')
    if selected_subject == 'all': selected_subject = None

    source_files = list(Student.objects.values_list('source_file', flat=True).distinct())
    if 'Unknown' in source_files and len(source_files) > 1:
        source_files.remove('Unknown')

    analytics = compute_analytics(selected_file, selected_subject)
    
    context = {
        'analytics': analytics,
        'role': get_role(request.user),
        'source_files': source_files,
        'selected_file': selected_file,
        'selected_subject': selected_subject,
    }
    return render(request, 'results/dashboard.html', context)


# ─────────────────────────────────────────
#  STUDENT VIEW
# ─────────────────────────────────────────

@login_required(login_url='/')
def student_view(request):
    role = get_role(request.user)
    user = request.user

    # Admins/teachers can also see this page but see all students summary
    if role in ('admin', 'teacher'):
        return redirect('/dashboard/')

    # Try to find student by matching username or full name
    student = None
    full_name = f"{user.first_name} {user.last_name}".strip()
    if full_name:
        student = Student.objects.filter(name__iexact=full_name).first()
    if not student:
        student = Student.objects.filter(name__iexact=user.username).first()

    results = []
    total_marks = 0
    pass_count = 0
    fail_count = 0

    if student:
        results = list(student.results.all().order_by('subject'))
        total_marks = sum(r.marks for r in results)
        pass_count = sum(1 for r in results if r.passed)
        fail_count = len(results) - pass_count

    context = {
        'student': student,
        'results': results,
        'total_marks': total_marks,
        'pass_count': pass_count,
        'fail_count': fail_count,
        'role': role,
    }
    return render(request, 'results/student.html', context)


# ─────────────────────────────────────────
#  EXPORT CSV
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
def export_csv_view(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="student_results.csv"'

    writer = csv.writer(response)
    writer.writerow(['Student Name', 'Gender', 'Category', 'Quota', 'Subject', 'Marks', 'Status'])

    results = Result.objects.select_related('student').all().order_by('student__name', 'subject')
    for r in results:
        writer.writerow([
            r.student.name,
            r.student.gender,
            r.student.category,
            r.student.quota,
            r.subject,
            r.marks,
            'Pass' if r.passed else 'Fail'
        ])

    return response


# ─────────────────────────────────────────
#  EXPORT PDF
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
def export_pdf_view(request):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                             rightMargin=0.5*inch, leftMargin=0.5*inch,
                             topMargin=0.75*inch, bottomMargin=0.75*inch)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Title'],
                                  fontSize=18, spaceAfter=6,
                                  textColor=colors.HexColor('#1e3a5f'))
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'],
                                     fontSize=10, spaceAfter=14,
                                     textColor=colors.HexColor('#555555'))
    section_style = ParagraphStyle('Section', parent=styles['Heading2'],
                                    fontSize=12, spaceBefore=14, spaceAfter=6,
                                    textColor=colors.HexColor('#1e3a5f'))

    elements = []
    elements.append(Paragraph("Advanced Result Analytics Suite", title_style))
    elements.append(Paragraph("Student Performance Report", subtitle_style))
    elements.append(Spacer(1, 0.1*inch))

    analytics = compute_analytics()

    if analytics:
        # Summary stats
        elements.append(Paragraph("Summary", section_style))
        summary_data = [
            ['Metric', 'Value'],
            ['Total Students', str(analytics['total_students'])],
            ['Total Subjects', str(analytics['total_subjects'])],
            ['Top Student', analytics['top_student']],
            ['Top Score', str(analytics['top_score'])],
            ['Pass Count', str(analytics['pass_count'])],
            ['Fail Count', str(analytics['fail_count'])],
            ['Pass Percentage', f"{analytics['pass_pct']}%"],
            ['Lowest Scoring Subject', analytics['lowest_subject']],
        ]
        t = Table(summary_data, colWidths=[2.5*inch, 4*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f4f8')]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.2*inch))

        # Subject averages
        elements.append(Paragraph("Subject-wise Average Marks", section_style))
        subj_data = [['Subject', 'Average Marks']]
        for subj, avg in analytics['subject_avg'].items():
            subj_data.append([subj, str(avg)])
        t2 = Table(subj_data, colWidths=[3.5*inch, 3*inch])
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#eff6ff')]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(t2)
        elements.append(Spacer(1, 0.2*inch))

        # Top 10
        elements.append(Paragraph("Top 10 Students by Total Marks", section_style))
        top10_data = [['Rank', 'Student Name', 'Total Marks']]
        for i, s in enumerate(analytics['top_10'], 1):
            top10_data.append([str(i), s['name'], str(s['total'])])
        t3 = Table(top10_data, colWidths=[0.6*inch, 4*inch, 2*inch])
        t3.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#7c3aed')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f3ff')]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(t3)
    else:
        elements.append(Paragraph("No data available. Please upload student results first.", styles['Normal']))

    doc.build(elements)
    pdf_value = buffer.getvalue()
    buffer.close()
    response = HttpResponse(pdf_value, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="analytics_report.pdf"'
    return response

# ─────────────────────────────────────────
#  NBA-SAR EXPORT
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
def export_nba_view(request):
    selected_file = request.GET.get('file')
    if selected_file == 'all': selected_file = None
    selected_subject = request.GET.get('subject')
    if selected_subject == 'all': selected_subject = None

    analytics = compute_analytics(selected_file, selected_subject)
    if not analytics:
        messages.error(request, "No data available to export.")
        return redirect('/dashboard/')
        
    import csv
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="NBA_SAR_Report.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['NBA-SAR Accreditation Report'])
    writer.writerow([])
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Total Students', analytics['total_students']])
    writer.writerow(['Success Index (SI)', analytics['nba_si']])
    writer.writerow(['Mean SGPA', analytics['mean_sgpa']])
    writer.writerow([])
    
    writer.writerow(['Category Performance'])
    writer.writerow(['Category', 'Pass %', 'Average SGPA'])
    for cat, metrics in analytics['cat_metrics'].items():
        writer.writerow([cat, metrics['pass_pct'], metrics['avg_sgpa']])
        
    writer.writerow([])
    writer.writerow(['Student Ranks & Backlogs'])
    writer.writerow(['Rank', 'USN', 'Name', 'SGPA', 'Total Marks'])
    for st in analytics['student_detail']:
        writer.writerow([st['rank'], st['usn'], st['name'], st['sgpa'], st['total']])
        
    return response

# ─────────────────────────────────────────
#  DELETE FILE
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
def delete_file_view(request):
    if request.method == 'POST':
        filename = request.POST.get('file_to_delete')
        if filename and filename != 'all':
            deleted_count, _ = Student.objects.filter(source_file=filename).delete()
            messages.success(request, f"Successfully deleted data for file: {filename} ({deleted_count} records removed).")
        elif filename == 'all':
            Student.objects.all().delete()
            messages.success(request, "Successfully deleted all student data.")
    return redirect('/dashboard/')
