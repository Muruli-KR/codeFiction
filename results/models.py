from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('teacher', 'Teacher'),
        ('student', 'Student'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='student')
    department = models.CharField(max_length=100, blank=True, null=True)
    student_class = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Student(models.Model):
    usn = models.CharField(max_length=50, blank=True, null=True)

    name = models.CharField(max_length=200)
    gender = models.CharField(max_length=20, default='Unknown')
    category = models.CharField(max_length=50, default='Unknown')  # raw value from file e.g. GM, 2A, SC
    quota = models.CharField(max_length=50, default='Unknown')     # raw value from file e.g. CET, COMEDK
    semester = models.CharField(max_length=20, default='1')
    sgpa = models.FloatField(default=0.0)
    source_file = models.CharField(max_length=255, default='Unknown')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def total_marks(self):
        return sum(r.marks for r in self.results.all())


class Result(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='results')
    subject = models.CharField(max_length=100)
    marks = models.FloatField(default=0)
    text_value = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.student.name} - {self.subject}: {self.marks}"

    @property
    def passed(self):
        return self.marks >= 35

class Backlog(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='backlogs')
    subject = models.CharField(max_length=100)
    semester = models.CharField(max_length=20)
    
    def __str__(self):
        return f"{self.student.name} - {self.subject} ({self.semester})"
