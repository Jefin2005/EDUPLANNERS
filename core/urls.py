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
    path('admin/departments/', views.manage_departments, name='manage_departments'),
    path('admin/semesters/', views.manage_semesters, name='manage_semesters'),
    path('admin/faculty/', views.manage_faculty, name='manage_faculty'),
    path('admin/subjects/', views.manage_subjects, name='manage_subjects'),
    path('admin/toggle-semester/', views.toggle_semester_mode, name='toggle_semester_mode'),
    path('admin/generate-timetable/', views.generate_timetable_view, name='generate_timetable'),
    path('admin/init-slots/', views.initialize_time_slots, name='init_time_slots'),
    
    # Faculty Dashboard
    path('faculty/', views.faculty_dashboard, name='faculty_dashboard'),
    path('faculty/preferences/', views.update_preferences, name='update_preferences'),
    
    # Timetable Views
    path('timetable/', views.timetable_view, name='timetable_view'),
    path('timetable/export/', views.export_timetable_pdf, name='export_timetable_pdf'),
]
