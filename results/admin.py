from django.contrib import admin
from .models import Profile, Student, Result


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'department', 'student_class']
    list_filter = ['role']
    search_fields = ['user__username']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['name', 'gender', 'category', 'quota', 'uploaded_by', 'created_at']
    list_filter = ['gender', 'category', 'quota']
    search_fields = ['name']


@admin.register(Result)
class ResultAdmin(admin.ModelAdmin):
    list_display = ['student', 'subject', 'marks', 'passed']
    list_filter = ['subject']
    search_fields = ['student__name', 'subject']
