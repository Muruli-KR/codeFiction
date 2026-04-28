from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('student/', views.student_view, name='student'),
    path('upload/', views.upload_view, name='upload'),
    path('export/', views.export_csv_view, name='export_csv'),
    path('export-pdf/', views.export_pdf_view, name='export_pdf'),
]
