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
    path('manage/teacher-lookup/', views.teacher_timetable_lookup, name='teacher_timetable_lookup'),
    
    # Teacher Dashboard
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('teacher/preferences/', views.update_preferences, name='update_preferences'),
    path('api/faculty/timetable/', views.faculty_timetable_api, name='faculty_timetable_api'),
    
    # Student Dashboard
    path('student/', views.student_dashboard, name='student_dashboard'),
    path('student/semesters/<int:department_id>/', views.get_semesters_for_department, name='get_semesters_for_department'),
    path('student/classes/<int:department_id>/<int:semester_num>/', views.get_class_sections, name='get_class_sections'),
    path('student/update-class/', views.update_student_class, name='update_student_class'),
    
    # Timetable Views
    path('timetable/', views.timetable_view, name='timetable_view'),
    path('timetable/export/', views.export_timetable_pdf, name='export_timetable_pdf'),
    
    # Timetable REST API
    path('api/timetable/departments/', views.api_timetable_departments, name='api_timetable_departments'),
    path('api/timetable/semesters/', views.api_timetable_semesters, name='api_timetable_semesters'),
    path('api/timetable/sections/', views.api_timetable_sections, name='api_timetable_sections'),
    path('api/timetable/grid/', views.api_timetable_grid, name='api_timetable_grid'),
    path('api/timetable/faculty-list/', views.api_timetable_faculty_list, name='api_timetable_faculty_list'),
    path('api/timetable/faculty-grid/', views.api_timetable_faculty_grid, name='api_timetable_faculty_grid'),

    # Teacher Timetable Lookup (AJAX)
    path('teacher-timetable/', views.teacher_timetable_page, name='teacher_timetable_page'),
    path('api/departments/', views.api_departments, name='api_departments'),
    path('api/teachers/', views.api_teachers_by_department, name='api_teachers'),
    path('api/timetable/teacher/', views.api_teacher_timetable, name='api_teacher_timetable'),
    path('api/faculty/preferences/update/', views.update_faculty_preferences_api, name='update_faculty_preferences_api'),

    # AI Assistant API
    path('api/ai/clashes/', views.ai_check_clashes, name='ai_check_clashes'),
    path('api/ai/suggest-faculty/', views.ai_suggest_faculty, name='ai_suggest_faculty'),
    path('api/ai/workload/', views.ai_workload, name='ai_workload'),
    path('api/ai/health/', views.ai_system_health, name='ai_system_health'),
    path('api/ai/search/', views.ai_search, name='ai_search'),
    path('api/ai/chat/', views.ai_chat_api, name='ai_chat_api'),
]
