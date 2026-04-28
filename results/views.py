import io
import csv
import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Sum, Avg, Count

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .models import Profile, Student, Result
from .forms import SignupForm, LoginForm, UploadForm


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
def upload_view(request):
    form = UploadForm()
    if request.method == 'POST':
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']
            filename = uploaded_file.name.lower()
            try:
                if filename.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                elif filename.endswith(('.xlsx', '.xls')):
                    df = pd.read_excel(uploaded_file)
                else:
                    messages.error(request, "Unsupported file format. Use CSV or Excel.")
                    return render(request, 'results/upload.html', {'form': form})

                df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]

                # Detect student name column
                name_col = next((c for c in df.columns if 'name' in c), None)
                gender_col = next((c for c in df.columns if 'gender' in c), None)
                category_col = next((c for c in df.columns if 'category' in c), None)
                quota_col = next((c for c in df.columns if 'quota' in c), None)

                if name_col is None:
                    messages.error(request, "CSV must have a 'Student Name' column.")
                    return render(request, 'results/upload.html', {'form': form})

                # Subject columns = all columns that are NOT meta columns
                meta_cols = {name_col, gender_col, category_col, quota_col}
                subject_cols = [c for c in df.columns if c not in meta_cols and c is not None]

                count = 0
                for _, row in df.iterrows():
                    student_name = str(row.get(name_col, '')).strip()
                    if not student_name or student_name.lower() == 'nan':
                        continue

                    gender = str(row.get(gender_col, 'Male')).strip() if gender_col else 'Male'
                    category = str(row.get(category_col, 'General')).strip() if category_col else 'General'
                    quota = str(row.get(quota_col, 'CET')).strip() if quota_col else 'CET'

                    # Normalize values
                    gender = gender if gender in ['Male', 'Female', 'Other'] else 'Male'
                    category = category if category in ['General', 'OBC', 'SC', 'ST', 'EWS'] else 'General'
                    quota = quota if quota in ['CET', 'COMEDK', 'Management'] else 'CET'

                    student, _ = Student.objects.get_or_create(
                        name=student_name,
                        defaults={
                            'gender': gender,
                            'category': category,
                            'quota': quota,
                            'uploaded_by': request.user,
                        }
                    )

                    for subj in subject_cols:
                        try:
                            marks = float(row[subj])
                        except (ValueError, TypeError):
                            marks = 0.0
                        Result.objects.update_or_create(
                            student=student,
                            subject=subj.replace('_', ' ').title(),
                            defaults={'marks': marks}
                        )
                    count += 1

                messages.success(request, f"Successfully imported {count} student records.")
                return redirect('/dashboard/')

            except Exception as e:
                messages.error(request, f"Error processing file: {str(e)}")

    return render(request, 'results/upload.html', {'form': form})


# ─────────────────────────────────────────
#  ANALYTICS ENGINE
# ─────────────────────────────────────────

def compute_analytics():
    results_qs = Result.objects.select_related('student').all()
    students_qs = Student.objects.all()

    if not results_qs.exists():
        return None

    data = list(results_qs.values('student__name', 'student__gender', 'student__category',
                                   'student__quota', 'subject', 'marks'))
    df = pd.DataFrame(data)
    df.columns = ['name', 'gender', 'category', 'quota', 'subject', 'marks']
    df['marks'] = pd.to_numeric(df['marks'], errors='coerce').fillna(0)

    # Subject averages
    subject_avg = df.groupby('subject')['marks'].mean().round(2).to_dict()

    # Total marks per student
    student_totals = df.groupby('name')['marks'].sum().round(2)
    top_student = student_totals.idxmax() if not student_totals.empty else 'N/A'
    top_score = student_totals.max() if not student_totals.empty else 0

    top_3 = student_totals.nlargest(3).reset_index()
    top_3.columns = ['name', 'total']
    top_3_list = top_3.to_dict('records')

    top_10 = student_totals.nlargest(10).reset_index()
    top_10.columns = ['name', 'total']
    top_10_list = top_10.to_dict('records')

    # Pass / Fail per result (marks >= 40)
    df['passed'] = df['marks'] >= 40
    pass_count = int(df['passed'].sum())
    fail_count = int((~df['passed']).sum())
    total_results = len(df)
    pass_pct = round(pass_count / total_results * 100, 2) if total_results else 0

    # Distributions
    gender_dist = df.drop_duplicates('name').groupby('gender').size().to_dict()
    category_dist = df.drop_duplicates('name').groupby('category').size().to_dict()
    quota_dist = df.drop_duplicates('name').groupby('quota').size().to_dict()

    # Lowest scoring subject
    lowest_subject = min(subject_avg, key=subject_avg.get) if subject_avg else 'N/A'
    lowest_avg = subject_avg.get(lowest_subject, 0)

    # Student detail table
    student_detail = df.groupby('name')['marks'].agg(['sum', 'mean', 'count']).round(2).reset_index()
    student_detail.columns = ['name', 'total', 'average', 'subjects']
    student_detail['passed_subjects'] = df[df['passed']].groupby('name').size().reindex(
        student_detail['name']).fillna(0).astype(int).values
    student_detail = student_detail.to_dict('records')

    return {
        'subject_avg': subject_avg,
        'top_student': top_student,
        'top_score': top_score,
        'top_3': top_3_list,
        'top_10': top_10_list,
        'pass_count': pass_count,
        'fail_count': fail_count,
        'pass_pct': pass_pct,
        'gender_dist': gender_dist,
        'category_dist': category_dist,
        'quota_dist': quota_dist,
        'lowest_subject': lowest_subject,
        'lowest_avg': lowest_avg,
        'student_detail': student_detail,
        'total_students': students_qs.count(),
        'total_subjects': len(subject_avg),
    }


# ─────────────────────────────────────────
#  DASHBOARD VIEW
# ─────────────────────────────────────────

@login_required(login_url='/')
@role_required('admin', 'teacher')
def dashboard_view(request):
    analytics = compute_analytics()
    context = {
        'analytics': analytics,
        'role': get_role(request.user),
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
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="analytics_report.pdf"'
    return response
