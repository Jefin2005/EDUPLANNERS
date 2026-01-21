from django.urls import path
from . import views

urlpatterns = [
    # Home
    path('', views.home, name='home'),
    
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Admin Dashboard
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('manage/departments/', views.manage_departments, name='manage_departments'),
    path('manage/departments/add/', views.add_department, name='add_department'),
    path('api/department-choices/', views.get_department_choices, name='get_department_choices'),
    path('manage/semesters/', views.manage_semesters, name='manage_semesters'),
    path('manage/semesters/add/', views.add_semester, name='add_semester'),
    path('manage/classes/add/', views.add_class, name='add_class'),
    path('manage/faculty/', views.manage_faculty, name='manage_faculty'),
    path('manage/subjects/', views.manage_subjects, name='manage_subjects'),
    path('manage/subjects/add/', views.add_subject, name='add_subject'),
    path('manage/subjects/<int:subject_id>/edit/', views.edit_subject, name='edit_subject'),
    path('manage/toggle-semester/', views.toggle_semester_mode, name='toggle_semester_mode'),
    path('manage/generate-timetable/', views.generate_timetable_view, name='generate_timetable'),
    path('manage/init-slots/', views.initialize_time_slots, name='init_time_slots'),
    
    # Faculty Dashboard
    path('faculty/', views.faculty_dashboard, name='faculty_dashboard'),
    path('faculty/preferences/', views.update_preferences, name='update_preferences'),
    
    # Timetable Views
    path('timetable/', views.timetable_view, name='timetable_view'),
    path('timetable/export/', views.export_timetable_pdf, name='export_timetable_pdf'),
]
